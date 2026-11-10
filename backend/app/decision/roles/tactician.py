from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

from ..data_services import DataCollector
from ..models import (
    StrategistInsightData,
    TacticianPlanData,
    PlanStepData,
    PlanExecutionLogData,
    AccountState,
)
from ...config import get_settings
from ...ai.factory import AIProviderFactory
from ...db.repository import (
    get_active_plan,
    save_tactician_plan,
    update_plan_status,
    save_decision,
)


def _ensure_aware(dt: datetime | None) -> datetime | None:
    """Ensure a datetime is timezone-aware (UTC if naive)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class TacticianRole:
    """Transforms strategist insights into normalized profit plans per symbol."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.collector = DataCollector()

    def run(
        self,
        symbol: str,
        strategist_insight: StrategistInsightData,
        account_state: AccountState | None = None,
        force: bool = False,
    ) -> TacticianPlanData:
        self.settings = get_settings()
        now = datetime.now(timezone.utc)
        existing_plan = get_active_plan(symbol)
        now = datetime.now(timezone.utc)
        if existing_plan and not force:
            if existing_plan.status in {"ready", "in_progress"}:
                return self._assemble_plan(existing_plan)
            if existing_plan.status == "hold":
                # Ensure next_eval_at is timezone-aware for comparison
                next_eval = _ensure_aware(existing_plan.next_eval_at)
                
                if next_eval and next_eval > now:
                    return self._assemble_plan(existing_plan)
                update_plan_status(existing_plan.id, "expired")

        if account_state is None:
            account_state = self.collector.build_account_state(self.settings.symbols)
        
        print(f"[Tactician] Account state balances: {account_state.balances}")
        print(f"[Tactician] Account state available_balances: {account_state.available_balances}")
        
        market_env = self.collector.build_market_envelope([symbol])

        payload = self._build_prompt_payload(symbol, strategist_insight, market_env, account_state)
        print(f"[Tactician] Prompt payload balances: {payload['balances']}")
        print(f"[Tactician] Prompt payload available_balances: {payload['available_balances']}")
        provider = self._make_provider(
            self.settings.tactician_provider, self.settings.tactician_model
        )
        prompt = provider.build_trading_prompt(payload)
        print(f"[Tactician] ▶ Calling provider={self.settings.tactician_provider}, model={self.settings.tactician_model} for {symbol}...")
        response = provider.invoke_json(prompt, schema_hint=self._schema())
        print(f"[Tactician] Raw response keys: {list(response.keys())}")

        plan = self._parse_plan(symbol, strategist_insight, response)
        parsed_plan_type = (plan.metadata or {}).get("plan_type") if plan.metadata else None
        print(f"[Tactician] Parsed plan: status={plan.status}, plan_type={parsed_plan_type}, steps={len(plan.steps)}")

        # Policy: Auto-upgrade from 'monitor/hold' to minimal action when strong bullish conditions exist
        try:
            # Auto-upgrade policy can be disabled via config
            if not self.settings.tactician_auto_upgrade_enabled:
                print("[Tactician] Auto-upgrade policy disabled by config; skipping.")
            elif plan.status == "hold":
                available_usdt = float(account_state.available_balances.get("USDT", 0) or 0)
                min_order = float(self.settings.tactician_auto_min_order_usd or 0)
                bullish_trend = str(strategist_insight.next_few_hours_trend or "").lower() in {"up", "bullish"}
                high_conf = float(strategist_insight.confidence or 0) >= float(self.settings.tactician_auto_confidence_threshold or 0)
                snapshot = market_env.snapshots.get(symbol)
                tf1h = snapshot.timeframes.get("1H") if snapshot else None
                tf15 = snapshot.timeframes.get("15m") if snapshot else None
                last_px = snapshot.last_price if snapshot else None
                above_res = False
                if tf1h and last_px is not None and tf1h.resistance is not None:
                    above_res = above_res or (last_px >= tf1h.resistance)
                if tf15 and last_px is not None and tf15.resistance is not None:
                    above_res = above_res or (last_px >= tf15.resistance)
                momentum_ok = ((tf1h and (tf1h.change_pct or 0) > 0) and (tf15 and (tf15.change_pct or 0) > 0)) or above_res

                if bullish_trend and high_conf and available_usdt >= min_order and momentum_ok:
                    size_cap = self.settings.max_position_usd or available_usdt
                    size_usd = max(min(available_usdt, size_cap), min_order)
                    chosen_type = "swing_hours" if (tf1h and (tf1h.change_pct or 0) >= (tf15.change_pct or 0)) else "scalp_quick"
                    step = PlanStepData(
                        id=None,
                        sequence_index=0,
                        action="enter",
                        side="buy",
                        entry_price=None,
                        exit_price=None,
                        stop_loss_price=None,
                        take_profit_price=None,
                        size_usd=size_usd,
                        size_pct=None,
                        leverage=self.settings.max_leverage if self.settings.margin_enabled else 1,
                        status="pending",
                        metadata={"auto_upgraded": True},
                    )
                    plan.steps = [step]
                    plan.status = "ready"
                    plan.metadata = {**(plan.metadata or {}), "plan_type": chosen_type, "auto_policy": "bullish_upgrade"}
                    extra = " Auto-upgraded from 'monitor' due to strong bullish conditions, sufficient funds, and momentum/resistance breakout."
                    plan.rationale = (plan.rationale or "") + extra
                    plan.confidence = max(float(plan.confidence or 0), float(strategist_insight.confidence or 0))
                    print(f"[Tactician] ↑ Auto-upgraded plan to action: type={chosen_type}, size_usd={size_usd:.2f}, leverage={step.leverage}")
        except Exception as _e:
            print(f"[Tactician] Auto-upgrade policy skipped due to error: {_e}")
        expires_at = now + timedelta(hours=6)
        next_eval_at = None
        if plan.status == "hold":
            next_eval_at = now + timedelta(minutes=self.settings.tactician_hold_retry_minutes)

        if strategist_insight.id is None:
            raise ValueError("Strategist insight must be persisted before creating a plan")

        record = save_tactician_plan(
            symbol=symbol,
            strategist_insight_id=strategist_insight.id,
            provider=plan.provider,
            model=plan.model,
            status=plan.status,
            confidence=plan.confidence,
            rationale=plan.rationale,
            raw_output=plan.raw_output,
            plan_metadata=plan.metadata,
            expires_at=expires_at,
            next_eval_at=next_eval_at,
            steps=[asdict(step) for step in plan.steps],
        )

        # Ensure decision record reflects the FINAL normalized plan (status/confidence/steps/plan_type)
        output_for_decision = dict(plan.raw_output or {})
        try:
            md = dict(output_for_decision.get("metadata") or {})
        except Exception:
            md = {}
        # Inject/override plan_type
        if isinstance(plan.metadata, dict) and plan.metadata.get("plan_type"):
            md["plan_type"] = plan.metadata.get("plan_type")
        # Also expose any auto policy tag for debugging/visibility
        if isinstance(plan.metadata, dict) and plan.metadata.get("auto_policy"):
            md["auto_policy"] = plan.metadata.get("auto_policy")
        output_for_decision["metadata"] = md
        output_for_decision["plan_type"] = md.get("plan_type") or output_for_decision.get("plan_type")
        # Override status/confidence/steps to match the normalized plan we will execute
        output_for_decision["status"] = plan.status
        output_for_decision["confidence"] = float(plan.confidence or 0)
        output_for_decision["steps"] = [
            {
                "sequence_index": s.sequence_index,
                "action": s.action,
                "side": s.side,
                "entry_price": s.entry_price,
                "exit_price": s.exit_price,
                "stop_loss_price": s.stop_loss_price,
                "take_profit_price": s.take_profit_price,
                "size_usd": s.size_usd,
                "size_pct": s.size_pct,
                "leverage": s.leverage,
            }
            for s in (plan.steps or [])
        ]
        output_for_decision["normalized"] = True

        save_decision(
            provider=plan.provider,
            ai_input=payload,
            ai_output=output_for_decision,
            execution_results=None,
            role="tactician",
        )

        assembled = self._assemble_plan(record)
        if assembled.status == "hold" and assembled.next_eval_at is None:
            assembled.next_eval_at = now + timedelta(minutes=self.settings.tactician_hold_retry_minutes)
        return assembled

    def _make_provider(self, provider_name: str, model: str | None):
        api_key = self._resolve_api_key(provider_name)
        return AIProviderFactory.create(provider_name, api_key=api_key, model=model)

    def _resolve_api_key(self, provider: str) -> str | None:
        if provider == "deepseek":
            return self.settings.deepseek_api_key
        if provider == "gemini":
            return self.settings.gemini_api_key
        raise ValueError(f"Unsupported tactician provider: {provider}")

    def _build_prompt_payload(
        self,
        symbol: str,
        strategist: StrategistInsightData,
        market_env,
        account_state: AccountState,
    ) -> Dict[str, Any]:
        snapshot = market_env.snapshots[symbol]
        plan_payload: Dict[str, Any] = {
            "role": "tactician",
            "timestamp": market_env.retrieved_at.isoformat(),
            "symbols": [symbol],
            "strategist": {
                "overall_trend": strategist.overall_trend,
                "next_days_trend": strategist.next_days_trend,
                "next_24h_trend": strategist.next_24h_trend,
                "next_few_hours_trend": strategist.next_few_hours_trend,
                "confidence": strategist.confidence,
                "rationale": strategist.rationale,
            },
            "market": {
                symbol: {
                    "last_price": snapshot.last_price,
                    "timeframes": {
                        tf: {
                            "change_pct": data.change_pct,
                            "support": data.support,
                            "resistance": data.resistance,
                            "bars": data.bars,
                        }
                        for tf, data in snapshot.timeframes.items()
                        if tf in ("1H", "15m")  # Tactician uses 1H and 15min data
                    },
                }
            },
            # Flatten account data for AI prompt builder
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
            "recent_trades": account_state.recent_trades,
            "hints": {
                "task": (
                    "You are a trading tactician. Based on the Strategist's trend and current 1H/15m action, design a concrete trading plan."
                    " Even if balance is low, still propose the best plan and set status='hold' ONLY if no clear setup exists.\n\n"
                    "Choose a plan_type EXACTLY ONE of:"
                    " - scalp_quick (fast in/out within minutes to <1h)"
                    " - swing_hours (hold 1-6h)"
                    " - build_only (accumulate position, no exits)"
                    " - exit_all (flatten exposure ASAP)\n\n"
                    "Return a JSON object with fields: status, confidence, rationale, plan_type, steps, metadata.\n\n"
                    "Rationale requirements (be concise but specific):"
                    " - Market structure and momentum (1H/15m)"
                    " - Support/resistance values actually used"
                    " - Risk factors and invalidation condition"
                    " - Balance constraints vs opportunity quality"
                    " - Why this plan_type and expected duration"
                ),
                "max_leverage": self.settings.max_leverage if self.settings.margin_enabled else 1,
                "min_order_usd": float(self.settings.tactician_auto_min_order_usd or 0),
            },
        }
        return plan_payload

    def _schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "confidence": {"type": "number"},
                "rationale": {"type": "string"},
                "plan_type": {"type": "string", "enum": ["scalp_quick", "swing_hours", "build_only", "exit_all", "monitor"]},
                "metadata": {"type": "object"},
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sequence_index": {"type": "integer"},
                            "action": {"type": "string"},
                            "side": {"type": "string"},
                            "entry_price": {"type": "number"},
                            "exit_price": {"type": "number"},
                            "stop_loss_price": {"type": "number"},
                            "take_profit_price": {"type": "number"},
                            "size_usd": {"type": "number"},
                            "size_pct": {"type": "number"},
                            "leverage": {"type": "integer"},
                        },
                        "required": ["action", "sequence_index"],
                    },
                },
            },
            "required": ["status", "confidence", "steps", "plan_type"],
        }

    def _parse_plan(
        self,
        symbol: str,
        strategist: StrategistInsightData,
        response: Dict[str, Any],
    ) -> TacticianPlanData:
        now = datetime.now(timezone.utc)
        status = response.get("status", "hold").lower()
        confidence = float(response.get("confidence", 0) or 0.0)
        rationale = response.get("rationale")
        metadata = response.get("metadata") or {}
        # Normalize plan_type into metadata
        plan_type = response.get("plan_type")
        if plan_type and isinstance(metadata, dict):
            metadata = {**metadata, "plan_type": str(plan_type)}
        # Fallback: if model didn't supply plan_type, infer a reasonable default
        if not plan_type:
            if status == "hold":
                metadata = {**(metadata or {}), "plan_type": "monitor"}
            else:
                # If there are steps but no explicit type, assume swing_hours by default
                metadata = {**(metadata or {}), "plan_type": "swing_hours"}

        steps_payload: List[Dict[str, Any]] = response.get("steps") or []
        steps: List[PlanStepData] = []
        for idx, step in enumerate(steps_payload):
            steps.append(
                PlanStepData(
                    id=None,
                    sequence_index=step.get("sequence_index", idx),
                    action=step.get("action", "enter").lower(),
                    side=(step.get("side") or "hold").lower(),
                    entry_price=self._safe_float(step.get("entry_price")),
                    exit_price=self._safe_float(step.get("exit_price")),
                    stop_loss_price=self._safe_float(step.get("stop_loss_price")),
                    take_profit_price=self._safe_float(step.get("take_profit_price")),
                    size_usd=self._safe_float(step.get("size_usd")),
                    size_pct=self._safe_float(step.get("size_pct")),
                    leverage=int(step.get("leverage", 1) or 1),
                    status="pending",
                    metadata=step.get("metadata"),
                )
            )

        provider_name = self.settings.tactician_provider
        model_name = self.settings.tactician_model

        return TacticianPlanData(
            id=None,
            symbol=symbol,
            provider=provider_name,
            model=model_name,
            strategist_insight_id=strategist.id,
            status=status,
            confidence=confidence,
            rationale=rationale,
            created_at=now,
            expires_at=now + timedelta(hours=6),
            next_eval_at=None,
            raw_output=response,
            metadata=metadata,
            steps=steps,
        )

    def _assemble_plan(self, record) -> TacticianPlanData:
        steps: List[PlanStepData] = []
        for step in record.plan_steps:
            steps.append(
                PlanStepData(
                    id=step.id,
                    sequence_index=step.sequence_index,
                    action=step.action,
                    side=step.side,
                    entry_price=step.entry_price,
                    exit_price=step.exit_price,
                    stop_loss_price=step.stop_loss_price,
                    take_profit_price=step.take_profit_price,
                    size_usd=step.size_usd,
                    size_pct=step.size_pct,
                    leverage=step.leverage,
                    status=step.status,
                    executed_at=step.executed_at,
                    metadata=step.step_metadata,
                )
            )

        logs = [
            PlanExecutionLogData(
                id=log.id,
                step_id=log.step_id,
                status=log.status,
                message=log.message,
                payload=log.payload,
                created_at=log.created_at,
            )
            for log in record.execution_logs
        ]

        return TacticianPlanData(
            id=record.id,
            symbol=record.symbol,
            provider=record.provider,
            model=record.model,
            strategist_insight_id=record.strategist_insight_id,
            status=record.status,
            confidence=record.confidence,
            rationale=record.rationale,
            created_at=record.created_at,
            expires_at=record.expires_at,
            next_eval_at=record.next_eval_at,
            raw_output=record.raw_output,
            metadata=record.plan_metadata,
            steps=steps,
            execution_logs=logs,
        )

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None


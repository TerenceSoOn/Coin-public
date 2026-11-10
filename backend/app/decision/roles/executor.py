from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

from ..data_services import DataCollector
from ..models import TacticianPlanData, ExecutorDecision, PlanStepData, AccountState
from ...config import get_settings
from ...ai.factory import AIProviderFactory
from ...db.repository import (
    update_plan_status,
    update_plan_step_status,
    append_execution_log,
    save_decision,
    get_active_plan,
)
from ...decision.executor import execute_decisions as legacy_execute_decisions


class ExecutorRole:
    """Executes tactician plan steps using AI for adaptive adjustments."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.collector = DataCollector()

    def run(self, plan: TacticianPlanData) -> ExecutorDecision | None:
        self.settings = get_settings()
        print(f"[Executor] ▶ run(plan_id={plan.id}, symbol={plan.symbol}, status={plan.status}, plan_type={(plan.metadata or {}).get('plan_type') if plan.metadata else None})")
        if plan.status not in {"ready", "in_progress"}:
            print("[Executor] Plan not executable at this time; skipping")
            return None

        # Check if a newer plan exists (interruption detection)
        newer_plan = get_active_plan(plan.symbol)
        is_interrupted = (
            newer_plan is not None 
            and newer_plan.id != plan.id 
            and newer_plan.created_at > plan.created_at
        )

        account_state = self.collector.build_account_state([plan.symbol])
        market_env = self.collector.build_market_envelope([plan.symbol])

        pending_steps = [step for step in plan.steps if step.status == "pending"]
        now = datetime.now(timezone.utc)

        # If plan is exit_all and there is no position on this symbol, complete immediately
        plan_type = (plan.metadata or {}).get("plan_type") if plan.metadata else None
        pos = account_state.positions.get(plan.symbol)
        if plan_type == "exit_all" and (pos is None or (pos.amount or 0) <= 0):
            print("[Executor] Plan 'exit_all' but no position present; marking completed")
            update_plan_status(plan_id=plan.id, status="completed", next_eval_at=None)
            return None
        
        # If interrupted by newer plan, trigger quick exit strategy
        if is_interrupted and plan.status == "in_progress":
            print(f"[Executor] Detected newer plan #{newer_plan.id}; executing quick exit")
            return self._execute_quick_exit(plan, account_state, market_env, newer_plan.id)
        
        if plan.status == "in_progress" and plan.next_eval_at and plan.next_eval_at > now:
            print(f"[Executor] Waiting until next_eval_at={plan.next_eval_at} to continue execution")
            return None
        if not pending_steps:
            print("[Executor] No pending steps; completing plan")
            update_plan_status(plan_id=plan.id, status="completed")
            return None

        if plan.id is not None:
            update_plan_status(plan_id=plan.id, status="in_progress")

        payload = self._build_prompt_payload(plan, pending_steps, market_env, account_state)
        provider = self._make_provider(
            self.settings.executor_provider, self.settings.executor_model
        )
        prompt = provider.build_trading_prompt(payload)
        print(f"[Executor] Calling provider={self.settings.executor_provider}, model={self.settings.executor_model}")
        structured = provider.invoke_json(prompt, schema_hint=self._schema())
        print(f"[Executor] Provider returned keys: {list(structured.keys())}")
        decision = self._parse_decision(plan, pending_steps, structured)

        save_decision(
            provider=decision.provider,
            ai_input=payload,
            ai_output=decision.raw_output,
            execution_results=None,
            role="executor",
        )

        execution_results = legacy_execute_decisions(
            {"actions": [dict(order) for order in decision.orders]},
            market_data={plan.symbol: {"last_price": market_env.snapshots[plan.symbol].last_price}},
        )

        if not decision.orders:
            retry_at = now + timedelta(minutes=self.settings.executor_hold_retry_minutes)
            append_execution_log(
                plan_id=plan.id,
                step_id=pending_steps[0].id if pending_steps else None,
                provider=decision.provider,
                model=decision.model,
                status="skipped",
                message="Executor generated no orders; holding plan",
                payload={"results": execution_results},
            )
            update_plan_status(plan_id=plan.id, status="hold", next_eval_at=retry_at)
            return decision

        for order, step in zip(decision.orders, pending_steps):
            status = "filled" if any(r.get("status") == "executed" for r in execution_results) else "placed"
            update_plan_step_status(
                step_id=step.id if step.id is not None else step.sequence_index,
                status=status,
                executed_at=now,
                metadata={"executor_order": order},
            )

        append_execution_log(
            plan_id=plan.id,
            step_id=pending_steps[0].id if pending_steps[0].id is not None else pending_steps[0].sequence_index,
            provider=decision.provider,
            model=decision.model,
            status="executed",
            message=decision.rationale,
            payload={"orders": decision.orders, "results": execution_results},
        )

        any_fail = any(r.get("status") != "executed" for r in execution_results)
        all_filled = execution_results and all(r.get("status") == "executed" for r in execution_results)

        if any_fail:
            retry_at = now + timedelta(minutes=self.settings.executor_hold_retry_minutes)
            update_plan_status(plan_id=plan.id, status="hold", next_eval_at=retry_at)
        elif all_filled and not pending_steps[1:]:
            update_plan_status(plan_id=plan.id, status="completed", next_eval_at=None)
        else:
            update_plan_status(plan_id=plan.id, status="in_progress")

        return decision

    def _execute_quick_exit(
        self,
        plan: TacticianPlanData,
        account_state: AccountState,
        market_env,
        newer_plan_id: int,
    ) -> ExecutorDecision | None:
        """Execute quick exit strategy when interrupted by newer plan."""
        now = datetime.now(timezone.utc)
        
        # Build payload for quick exit decision
        payload = self._build_quick_exit_payload(plan, market_env, account_state, newer_plan_id)
        
        provider = self._make_provider(
            self.settings.executor_provider, self.settings.executor_model
        )
        prompt = provider.build_trading_prompt(payload)
        structured = provider.invoke_json(prompt, schema_hint=self._schema())
        decision = self._parse_decision(plan, plan.steps, structured)

        save_decision(
            provider=decision.provider,
            ai_input=payload,
            ai_output=decision.raw_output,
            execution_results=None,
            role="executor_quick_exit",
        )

        execution_results = legacy_execute_decisions(
            {"actions": [dict(order) for order in decision.orders]},
            market_data={plan.symbol: {"last_price": market_env.snapshots[plan.symbol].last_price}},
        )

        append_execution_log(
            plan_id=plan.id,
            step_id=None,
            provider=decision.provider,
            model=decision.model,
            status="interrupted",
            message=f"Quick exit executed due to newer plan #{newer_plan_id}. Rationale: {decision.rationale}",
            payload={"orders": decision.orders, "results": execution_results, "newer_plan_id": newer_plan_id},
        )

        # Mark old plan as superseded
        update_plan_status(plan_id=plan.id, status="superseded", next_eval_at=None)
        
        return decision

    def _make_provider(self, provider_name: str, model: str | None):
        api_key = self._resolve_api_key(provider_name)
        return AIProviderFactory.create(provider_name, api_key=api_key, model=model)

    def _resolve_api_key(self, provider: str) -> str | None:
        if provider == "deepseek":
            return self.settings.deepseek_api_key
        if provider == "gemini":
            return self.settings.gemini_api_key
        raise ValueError(f"Unsupported executor provider: {provider}")

    def _build_prompt_payload(
        self,
        plan: TacticianPlanData,
        steps: List[PlanStepData],
        market_env,
        account_state: AccountState,
    ) -> Dict[str, Any]:
        snapshot = market_env.snapshots[plan.symbol]
        # Derive execution profile based on plan_type
        plan_type = (plan.metadata or {}).get("plan_type") if plan.metadata else None
        # Defaults from settings
        deviation = self.settings.executor_deviation_threshold_pct
        timeout_min = self.settings.plan_execution_timeout_minutes
        order_pref = "limit_or_market"
        speed_priority = False

        if plan_type == "scalp_quick":
            deviation = min(deviation, 0.5)
            timeout_min = min(timeout_min, 60)
            order_pref = "market_preferred"
            speed_priority = True
        elif plan_type == "swing_hours":
            deviation = max(deviation, self.settings.executor_deviation_threshold_pct)
            timeout_min = max(timeout_min, 360)
        elif plan_type == "build_only":
            order_pref = "limit_preferred"
        elif plan_type == "exit_all":
            order_pref = "market_only"
            speed_priority = True

        return {
            "role": "executor",
            "timestamp": market_env.retrieved_at.isoformat(),
            "symbols": [plan.symbol],
            "plan": {
                "id": plan.id,
                "status": plan.status,
                "confidence": plan.confidence,
                "rationale": plan.rationale,
                "plan_type": plan_type,
                "steps": [
                    {
                        "sequence_index": step.sequence_index,
                        "action": step.action,
                        "side": step.side,
                        "entry_price": step.entry_price,
                        "exit_price": step.exit_price,
                        "stop_loss_price": step.stop_loss_price,
                        "take_profit_price": step.take_profit_price,
                        "size_usd": step.size_usd,
                        "size_pct": step.size_pct,
                        "leverage": step.leverage,
                    }
                    for step in steps
                ],
            },
            "market": {
                plan.symbol: {
                    "last_price": snapshot.last_price,
                    "timeframes": {
                        tf: {
                            "change_pct": data.change_pct,
                            "support": data.support,
                            "resistance": data.resistance,
                            "bars": data.bars,
                        }
                        for tf, data in snapshot.timeframes.items()
                        if tf in ("1H", "15m", "5m", "1m")
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
                    "You are a trade executor. Execute the Tactician's plan step by step while adapting to real-time conditions. "
                    "If market deviates significantly (>={deviation}%), reduce risk or pause and signal re-evaluation."
                ).format(deviation=deviation),
                "executor_deviation_threshold_pct": deviation,
                "plan_timeout_minutes": timeout_min,
                "plan_type": plan_type,
                "order_preference": order_pref,
                "priority": "speed_over_profit" if speed_priority else "balanced",
            },
        }

    def _build_quick_exit_payload(
        self,
        plan: TacticianPlanData,
        market_env,
        account_state: AccountState,
        newer_plan_id: int,
    ) -> Dict[str, Any]:
        """Build payload for quick exit when interrupted by newer plan."""
        snapshot = market_env.snapshots[plan.symbol]
        
        return {
            "role": "executor_quick_exit",
            "timestamp": market_env.retrieved_at.isoformat(),
            "symbols": [plan.symbol],
            "interruption": {
                "old_plan_id": plan.id,
                "newer_plan_id": newer_plan_id,
                "message": "A newer Tactician plan has been created. You MUST quickly exit current positions.",
            },
            "old_plan": {
                "id": plan.id,
                "status": plan.status,
                "confidence": plan.confidence,
                "rationale": plan.rationale,
                "pending_steps": [
                    step.action for step in plan.steps if step.status == "pending"
                ],
            },
            "market": {
                plan.symbol: {
                    "last_price": snapshot.last_price,
                    "timeframes": {
                        tf: {
                            "change_pct": data.change_pct,
                            "support": data.support,
                            "resistance": data.resistance,
                        }
                        for tf, data in snapshot.timeframes.items()
                        if tf in ("15m", "5m", "1m")  # Only short-term data for quick exit
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
            "recent_trades": account_state.recent_trades[-5:] if account_state.recent_trades else [],  # Only last 5 trades
            "hints": {
                "task": "URGENT: A newer trading plan (#{newer_plan_id}) has been created. You MUST immediately close current positions to prepare for the new plan. Design quick exit trades (market orders preferred) to flatten positions REGARDLESS of profit/loss. Speed is critical - exit now, analyze later. After positions are closed, the newer plan will be executed.".format(newer_plan_id=newer_plan_id),
                "exit_strategy": "market_orders",
                "priority": "speed_over_profit",
            },
        }

    def _schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "orders": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "action": {"type": "string"},
                            "size_usd": {"type": "number"},
                            "size_pct": {"type": "number"},
                            "leverage": {"type": "integer"},
                            "reason": {"type": "string"},
                        },
                        "required": ["symbol", "action"],
                    },
                },
                "rationale": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["orders"],
        }

    def _parse_decision(
        self,
        plan: TacticianPlanData,
        steps: List[PlanStepData],
        response: Dict[str, Any],
    ) -> ExecutorDecision:
        orders = response.get("orders") or []
        normalized_orders: List[Dict[str, Any]] = []
        for step, order in zip(steps, orders):
            normalized_orders.append(
                {
                    "step_sequence": step.sequence_index,
                    "symbol": plan.symbol,
                    "action": order.get("action", step.action),
                    "size_usd": float(order.get("size_usd", step.size_usd or 0) or 0.0),
                    "size_pct": float(order.get("size_pct", step.size_pct or 0) or 0.0),
                    "leverage": int(order.get("leverage", step.leverage or 1) or 1),
                    "reason": order.get("reason"),
                }
            )

        primary_step_id = steps[0].id if steps and steps[0].id is not None else (steps[0].sequence_index if steps else None)

        return ExecutorDecision(
            plan_id=plan.id if plan.id is not None else -1,
            step_id=primary_step_id,
            provider=self.settings.executor_provider,
            model=self.settings.executor_model,
            status="prepared",
            orders=normalized_orders,
            rationale=response.get("rationale"),
            confidence=float(response.get("confidence", 0) or 0.0),
            raw_output=response,
        )


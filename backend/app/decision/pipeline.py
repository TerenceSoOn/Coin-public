from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from ..config import get_settings
from ..db.repository import update_plan_status
from .models import ExecutorDecision, StrategistInsightData, TacticianPlanData
from .roles.executor import ExecutorRole
from .roles.strategist import StrategistRole
from .roles.tactician import TacticianRole


@dataclass(slots=True)
class PipelineResult:
    symbol: str
    strategist: Optional[StrategistInsightData]
    plan: Optional[TacticianPlanData]
    execution: Optional[ExecutorDecision]
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "symbol": self.symbol,
            "error": self.error,
            "strategist": self._serialize_strategist(self.strategist),
            "plan": self._serialize_plan(self.plan),
            "execution": self._serialize_execution(self.execution),
        }

    @staticmethod
    def _serialize_strategist(insight: Optional[StrategistInsightData]) -> Optional[Dict[str, object]]:
        if insight is None:
            return None
        return {
            "id": insight.id,
            "symbol": insight.symbol,
            "provider": insight.provider,
            "model": insight.model,
            "generated_at": insight.generated_at.isoformat() if insight.generated_at else None,
            "expires_at": insight.expires_at.isoformat() if insight.expires_at else None,
            "overall_trend": insight.overall_trend,
            "next_days_trend": insight.next_days_trend,
            "next_24h_trend": insight.next_24h_trend,
            "next_few_hours_trend": insight.next_few_hours_trend,
            "confidence": insight.confidence,
            "rationale": insight.rationale,
            "raw_output": insight.raw_output,
        }

    @staticmethod
    def _serialize_plan(plan: Optional[TacticianPlanData]) -> Optional[Dict[str, object]]:
        if plan is None:
            return None
        return {
            "id": plan.id,
            "symbol": plan.symbol,
            "status": plan.status,
            "provider": plan.provider,
            "model": plan.model,
            "confidence": plan.confidence,
            "rationale": plan.rationale,
            "created_at": plan.created_at.isoformat() if plan.created_at else None,
            "expires_at": plan.expires_at.isoformat() if plan.expires_at else None,
            "next_eval_at": plan.next_eval_at.isoformat() if plan.next_eval_at else None,
            "steps": [
                {
                    "id": step.id,
                    "sequence_index": step.sequence_index,
                    "action": step.action,
                    "side": step.side,
                    "status": step.status,
                    "size_usd": step.size_usd,
                    "size_pct": step.size_pct,
                    "entry_price": step.entry_price,
                    "exit_price": step.exit_price,
                    "stop_loss_price": step.stop_loss_price,
                    "take_profit_price": step.take_profit_price,
                    "executed_at": step.executed_at.isoformat() if step.executed_at else None,
                    "metadata": step.metadata,
                }
                for step in plan.steps
            ],
            "execution_logs": [
                {
                    "id": log.id,
                    "step_id": log.step_id,
                    "status": log.status,
                    "message": log.message,
                    "payload": log.payload,
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                }
                for log in plan.execution_logs[:20]
            ],
            "metadata": plan.metadata,
            "raw_output": plan.raw_output,
        }

    @staticmethod
    def _serialize_execution(decision: Optional[ExecutorDecision]) -> Optional[Dict[str, object]]:
        if decision is None:
            return None
        return {
            "plan_id": decision.plan_id,
            "step_id": decision.step_id,
            "provider": decision.provider,
            "model": decision.model,
            "status": decision.status,
            "confidence": decision.confidence,
            "rationale": decision.rationale,
            "orders": decision.orders,
        }


class PipelineCoordinator:
    """Coordinates strategist → tactician → executor workflow per symbol."""

    def __init__(self, max_workers: Optional[int] = None) -> None:
        self.settings = get_settings()
        self._explicit_max_workers = max_workers
        self.strategist = StrategistRole()
        self.tactician = TacticianRole()
        self.executor = ExecutorRole()

    def run_once(
        self,
        *,
        symbols: Optional[List[str]] = None,
        force_strategist: bool = False,
    ) -> Dict[str, object]:
        start = datetime.now(timezone.utc)
        self.settings = get_settings()
        target_symbols = symbols or self.settings.symbols
        max_workers = self._explicit_max_workers or self.settings.max_symbol_workers
        worker_count = max(1, min(len(target_symbols), max_workers))

        results: List[PipelineResult] = []
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {
                pool.submit(self._process_symbol, symbol, force_strategist): symbol
                for symbol in target_symbols
            }
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:  # pragma: no cover - should not occur
                    symbol = futures[future]
                    results.append(
                        PipelineResult(symbol=symbol, strategist=None, plan=None, execution=None, error=str(exc))
                    )

        duration_s = (datetime.now(timezone.utc) - start).total_seconds()
        return {
            "started_at": start.isoformat(),
            "duration_seconds": duration_s,
            "symbols": target_symbols,
            "results": [result.as_dict() for result in results],
        }

    def _process_symbol(self, symbol: str, force_strategist: bool) -> PipelineResult:
        try:
            insight = self.strategist.run(symbol, force=force_strategist)
        except Exception as exc:
            return PipelineResult(
                symbol=symbol,
                strategist=None,
                plan=None,
                execution=None,
                error=f"Strategist failed: {exc}",
            )

        plan: Optional[TacticianPlanData] = None
        try:
            plan = self.tactician.run(symbol, insight)
        except Exception as exc:
            return PipelineResult(
                symbol=symbol,
                strategist=insight,
                plan=None,
                execution=None,
                error=f"Tactician failed: {exc}",
            )

        # If tactician indicates hold, executor stays idle and plan is rescheduled later
        if plan and plan.status == "hold":
            # Ensure plan has a retry time for scheduler checks
            if plan.next_eval_at is None:
                retry = datetime.now(timezone.utc) + timedelta(minutes=self.settings.tactician_hold_retry_minutes)
                if plan.id:
                    update_plan_status(plan.id, "hold", next_eval_at=retry)
            return PipelineResult(
                symbol=symbol,
                strategist=insight,
                plan=plan,
                execution=None,
            )

        execution: Optional[ExecutorDecision] = None
        try:
            if plan and plan.status in {"ready", "in_progress"}:
                execution = self.executor.run(plan)
            elif plan and plan.status == "expired" and plan.id:
                update_plan_status(plan_id=plan.id, status="expired")
        except Exception as exc:
            return PipelineResult(
                symbol=symbol,
                strategist=insight,
                plan=plan,
                execution=None,
                error=f"Executor failed: {exc}",
            )

        return PipelineResult(
            symbol=symbol,
            strategist=insight,
            plan=plan,
            execution=execution,
        )
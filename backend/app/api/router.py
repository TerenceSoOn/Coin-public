from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Any, Dict
from ..config import get_settings, update_settings
from ..scheduler import update_ai_cadence
from ..decision.engine import run_decision_once, get_last_pipeline_run
from ..db.repository import (
    get_candles,
    get_recent_decisions,
    get_decisions_page,
    count_decisions,
    save_equity_snapshot,
    get_equity_snapshots,
    get_latest_strategist_insight,
    get_active_plan,
    get_execution_logs,
)
from ..okx.client import get_last_prices, get_tickers_map
from ..okx.trading_client import get_trading_client
import csv
import io
import json

api_router = APIRouter()

class ConfigUpdate(BaseModel):
    # Only include commonly edited fields for MVP
    # Mode is configured via .env only (sandbox|live)
    mode: str | None = None  # runtime updates ignored by UI
    paused: bool | None = None  # Pause/resume trading
    symbols: list[str] | None = None
    ai_cadence_minutes: int | None = None
    strategist_cadence_minutes: int | None = None
    price_spike_trigger_pct: float | None = None
    margin_enabled: bool | None = None
    max_leverage: int | None = None
    max_position_usd: float | None = None
    max_total_exposure_usd: float | None = None
    daily_loss_cap_usd: float | None = None
    # Provider-specific
    ai_provider: str | None = None
    deepseek_model: str | None = None
    gemini_model: str | None = None
    strategist_provider: str | None = None
    strategist_model: str | None = None
    tactician_provider: str | None = None
    tactician_model: str | None = None
    executor_provider: str | None = None
    executor_model: str | None = None
    strategist_cache_ttl_minutes: int | None = None
    tactician_hold_retry_minutes: int | None = None
    executor_hold_retry_minutes: int | None = None
    executor_deviation_threshold_pct: float | None = None
    plan_execution_timeout_minutes: int | None = None
    max_symbol_workers: int | None = None
    # Tactician auto-upgrade policy
    tactician_auto_upgrade_enabled: bool | None = None
    tactician_auto_confidence_threshold: float | None = None
    tactician_auto_min_order_usd: float | None = None

@api_router.get("/health")
def health() -> Dict[str, str]:
    """Health check endpoint"""
    return {"status": "ok"}

@api_router.get("/config")
def get_config() -> Dict[str, Any]:
    return get_settings().model_dump()

@api_router.put("/config")
def put_config(payload: ConfigUpdate) -> Dict[str, Any]:
    try:
        # Capture old cadence to detect changes
        old = get_settings()
        old_cadence = old.ai_cadence_minutes
        new_settings = update_settings(**payload.model_dump(exclude_none=True))
        # If cadence changed, reschedule AI job
        if payload.ai_cadence_minutes is not None and new_settings.ai_cadence_minutes != old_cadence:
            update_ai_cadence()
        return new_settings.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@api_router.post("/decision/run")
def api_run_decision(symbols: list[str] | None = None, force: bool = False):
    result = run_decision_once(symbols=symbols, force_strategist=force)
    return result


@api_router.post("/decision/run-strategist")
def api_run_strategist(symbols: list[str] | None = None):
    """Run only the Strategist role to generate market insights."""
    from ..decision.roles.strategist import StrategistRole
    s = get_settings()
    target_symbols = symbols or s.symbols
    role = StrategistRole()
    
    results = []
    for symbol in target_symbols:
        try:
            insight = role.run(symbol, force=True)
            results.append({
                "symbol": symbol,
                "status": "success",
                "insight": {
                    "id": insight.id,
                    "provider": insight.provider,
                    "model": insight.model,
                    "overall_trend": insight.overall_trend,
                    "confidence": insight.confidence,
                    "rationale": insight.rationale,
                }
            })
        except Exception as e:
            results.append({"symbol": symbol, "status": "error", "error": str(e)})
    
    return {"role": "strategist", "results": results}


@api_router.post("/decision/run-predictor")
def api_run_predictor(symbols: list[str] | None = None, force: bool = True):
    """Run only the Predictor role to generate market predictions (V2 agent)."""
    from ..decision.roles.predictor import PredictorRole
    s = get_settings()
    target_symbols = symbols or s.symbols
    role = PredictorRole()
    
    results = []
    for symbol in target_symbols:
        try:
            insight = role.run(symbol, force=force)
            results.append({
                "symbol": symbol,
                "status": "success",
                "insight": {
                    "id": insight.id,
                    "provider": insight.provider,
                    "model": insight.model,
                    "overall_trend": insight.overall_trend,
                    "trend_strength": insight.trend_strength,
                    "next_24h_bias": insight.next_24h_bias,
                    "next_4h_bias": insight.next_4h_bias,
                    "support_levels": insight.support_levels,
                    "resistance_levels": insight.resistance_levels,
                    "confidence": insight.confidence,
                    "reasoning": insight.reasoning,
                }
            })
        except Exception as e:
            import traceback
            results.append({
                "symbol": symbol,
                "status": "error",
                "error": str(e),
                "traceback": traceback.format_exc()
            })
    
    return {"role": "predictor", "results": results}


@api_router.get("/predictor/{symbol}")
def get_predictor_insight(symbol: str):
    """Get the latest Predictor insight for a symbol."""
    from ..db.repository import get_latest_predictor_insight
    
    record = get_latest_predictor_insight(symbol)
    if not record:
        raise HTTPException(status_code=404, detail="No predictor insight found")
    
    return {
        "id": record.id,
        "symbol": record.symbol,
        "provider": record.provider,
        "model": record.model,
        "generated_at": record.generated_at.isoformat() if record.generated_at else None,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "overall_trend": record.overall_trend,
        "trend_strength": record.trend_strength,
        "next_24h_bias": record.next_24h_bias,
        "next_4h_bias": record.next_4h_bias,
        "support_levels": record.support_levels,
        "resistance_levels": record.resistance_levels,
        "confidence": record.confidence,
        "reasoning": record.reasoning,
        "news_context": record.news_context,
        "raw_output": record.raw_output,
    }


@api_router.post("/decision/run-trader")
def api_run_trader(symbols: list[str] | None = None, force: bool = True):
    """Run only the Trader role to make execution decisions (V2 agent)."""
    from ..decision.roles.trader import TraderRole
    s = get_settings()
    target_symbols = symbols or s.symbols
    role = TraderRole()
    
    results = []
    for symbol in target_symbols:
        try:
            decision = role.run(symbol, force=force)
            results.append({
                "symbol": symbol,
                "status": "success",
                "decision": {
                    "id": decision.id,
                    "action": decision.action,
                    "side": decision.side,
                    "entry_price": decision.entry_price,
                    "size_usd": decision.size_usd,
                    "leverage": decision.leverage,
                    "stop_loss": decision.stop_loss,
                    "take_profit": decision.take_profit,
                    "confidence": decision.confidence,
                    "reason": decision.reason,
                    "invalidation_condition": decision.invalidation_condition,
                    "memory_summary": decision.memory_summary,
                    "status": decision.status,
                    "executed_at": decision.executed_at.isoformat() if decision.executed_at else None,
                    "execution_result": decision.execution_result,
                }
            })
        except Exception as e:
            import traceback
            results.append({
                "symbol": symbol,
                "status": "error",
                "error": str(e),
                "traceback": traceback.format_exc()
            })
    
    return {"role": "trader", "results": results}


@api_router.get("/trader/{symbol}")
def get_trader_decision(symbol: str):
    """Get the latest Trader decision for a symbol."""
    from ..db.repository import get_latest_trader_decision
    
    record = get_latest_trader_decision(symbol)
    if not record:
        raise HTTPException(status_code=404, detail="No trader decision found")
    
    return {
        "id": record.id,
        "symbol": record.symbol,
        "predictor_insight_id": record.predictor_insight_id,
        "provider": record.provider,
        "model": record.model,
        "action": record.action,
        "side": record.side,
        "entry_price": record.entry_price,
        "size_usd": record.size_usd,
        "leverage": record.leverage,
        "stop_loss": record.stop_loss,
        "take_profit": record.take_profit,
        "confidence": record.confidence,
        "reason": record.reason,
        "invalidation_condition": record.invalidation_condition,
        "memory_summary": record.memory_summary,
        "status": record.status,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "executed_at": record.executed_at.isoformat() if record.executed_at else None,
        "execution_result": record.execution_result,
        "raw_output": record.raw_output,
    }


@api_router.post("/decision/run-tactician")
def api_run_tactician(symbols: list[str] | None = None, force: bool = True):
    """
    Run only the Tactician role to create trading plans.
    
    This endpoint creates a new plan immediately without checking if previous plans
    are still executing. When Executor detects a newer plan, it will automatically
    execute a quick exit strategy to close current positions before starting the new plan.
    """
    from ..decision.roles.tactician import TacticianRole
    from ..decision.data_services import DataCollector
    s = get_settings()
    target_symbols = symbols or s.symbols
    collector = DataCollector()
    role = TacticianRole()
    
    results = []
    for symbol in target_symbols:
        try:
            # Get the latest strategist insight for this symbol
            insight_record = get_latest_strategist_insight(symbol)
            if not insight_record:
                results.append({"symbol": symbol, "status": "error", "error": "No strategist insight available"})
                continue
            
            from ..decision.models import StrategistInsightData
            insight = StrategistInsightData(
                id=insight_record.id,
                symbol=insight_record.symbol,
                provider=insight_record.provider,
                model=insight_record.model,
                generated_at=insight_record.generated_at,
                overall_trend=insight_record.overall_trend,
                next_days_trend=insight_record.next_days_trend,
                next_24h_trend=insight_record.next_24h_trend,
                next_few_hours_trend=insight_record.next_few_hours_trend,
                confidence=insight_record.confidence,
                rationale=insight_record.rationale,
                expires_at=insight_record.expires_at,
                raw_output=insight_record.raw_output,
            )
            
            # Build account state for tactician
            account_state = collector.build_account_state([symbol])
            plan = role.run(symbol, insight, account_state, force=force)
            
            results.append({
                "symbol": symbol,
                "status": "success",
                "plan": {
                    "id": plan.id,
                    "status": plan.status,
                    "confidence": plan.confidence,
                    "rationale": plan.rationale,
                }
            })
        except Exception as e:
            results.append({"symbol": symbol, "status": "error", "error": str(e)})
    
    return {"role": "tactician", "results": results}


@api_router.post("/decision/run-predictor")
def api_run_predictor(symbols: list[str] | None = None):
    """Run only the Predictor role to generate macro insights (1D/4H).
    """
    from ..decision.roles.predictor import PredictorRole
    s = get_settings()
    target_symbols = symbols or s.symbols
    role = PredictorRole()

    results = []
    for symbol in target_symbols:
        try:
            insight = role.run(symbol, force=True)
            results.append({
                "symbol": symbol,
                "status": "success",
                "insight": {
                    "id": insight.id,
                    "provider": insight.provider,
                    "model": insight.model,
                    "overall_trend": insight.overall_trend,
                    "trend_strength": insight.trend_strength,
                    "next_24h_bias": insight.next_24h_bias,
                    "next_4h_bias": insight.next_4h_bias,
                    "confidence": insight.confidence,
                },
            })
        except Exception as e:
            results.append({"symbol": symbol, "status": "error", "error": str(e)})

    return {"role": "predictor", "results": results}


@api_router.post("/decision/run-trader")
def api_run_trader(symbols: list[str] | None = None, force: bool = True):
    """Run only the Trader role to generate execution decisions and optionally execute them.
    """
    from ..decision.roles.trader import TraderRole
    from ..decision.data_services import DataCollector
    s = get_settings()
    target_symbols = symbols or s.symbols
    collector = DataCollector()
    role = TraderRole()

    results = []
    for symbol in target_symbols:
        try:
            # Get latest predictor insight
            from ..db.repository import get_latest_predictor_insight
            insight = get_latest_predictor_insight(symbol)
            if not insight:
                results.append({"symbol": symbol, "status": "error", "error": "No predictor insight available"})
                continue

            # Build account snapshot
            account_state = collector.build_account_state([symbol])
            decision = role.run(symbol, predictor_insight=insight, force=force)

            results.append({
                "symbol": symbol,
                "status": "success",
                "decision": {
                    "id": decision.id,
                    "action": decision.action,
                    "size_usd": decision.size_usd,
                    "confidence": decision.confidence,
                    "status": decision.status,
                },
            })
        except Exception as e:
            results.append({"symbol": symbol, "status": "error", "error": str(e)})

    return {"role": "trader", "results": results}


@api_router.get("/predictor/{symbol}")
def get_predictor_insight(symbol: str):
    from ..db.repository import get_latest_predictor_insight
    record = get_latest_predictor_insight(symbol)
    if not record:
        raise HTTPException(status_code=404, detail="No predictor insight found")
    return {
        "id": record.id,
        "symbol": record.symbol,
        "provider": record.provider,
        "model": record.model,
        "generated_at": record.generated_at.isoformat() if record.generated_at else None,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "overall_trend": record.overall_trend,
        "trend_strength": record.trend_strength,
        "next_24h_bias": record.next_24h_bias,
        "next_4h_bias": record.next_4h_bias,
        "confidence": record.confidence,
        "reasoning": record.reasoning,
        "support_levels": record.support_levels,
        "resistance_levels": record.resistance_levels,
        "news_context": record.news_context,
        "raw_output": record.raw_output,
    }


@api_router.get("/trader/{symbol}")
def get_trader_decision(symbol: str):
    from ..db.repository import get_latest_trader_decision
    record = get_latest_trader_decision(symbol)
    if not record:
        raise HTTPException(status_code=404, detail="No trader decision found")
    return {
        "id": record.id,
        "symbol": record.symbol,
        "action": record.action,
        "size_usd": record.size_usd,
        "confidence": record.confidence,
        "status": record.status,
        "memory_summary": record.memory_summary,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "execution_result": record.execution_result,
        "raw_output": record.raw_output,
    }


@api_router.post("/decision/run-executor")
def api_run_executor(symbols: list[str] | None = None):
    """
    Run only the Executor role to execute trading plans.
    
    Executor automatically detects if a newer plan exists while executing the current plan.
    If interrupted, it will execute a quick exit strategy (market orders to close positions)
    before the newer plan can be executed.
    """
    from ..decision.roles.executor import ExecutorRole
    s = get_settings()
    target_symbols = symbols or s.symbols
    role = ExecutorRole()
    
    results = []
    for symbol in target_symbols:
        try:
            # Get the active plan for this symbol
            plan_record = get_active_plan(symbol)
            if not plan_record:
                results.append({"symbol": symbol, "status": "error", "error": "No active plan"})
                continue
            
            # Build TacticianPlanData from record
            from ..decision.models import TacticianPlanData, PlanStepData, PlanExecutionLogData
            
            steps = []
            for step in plan_record.plan_steps:
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
                for log in plan_record.execution_logs
            ]
            
            plan = TacticianPlanData(
                id=plan_record.id,
                symbol=plan_record.symbol,
                provider=plan_record.provider,
                model=plan_record.model,
                strategist_insight_id=plan_record.strategist_insight_id,
                status=plan_record.status,
                confidence=plan_record.confidence,
                rationale=plan_record.rationale,
                created_at=plan_record.created_at,
                expires_at=plan_record.expires_at,
                next_eval_at=plan_record.next_eval_at,
                raw_output=plan_record.raw_output,
                metadata=plan_record.plan_metadata,
                steps=steps,
                execution_logs=logs,
            )
            
            execution = role.run(plan)
            
            results.append({
                "symbol": symbol,
                "status": "success",
                "execution": {
                    "id": execution.id if execution and execution.id else "pending",
                    "status": execution.status if execution else "pending",
                }
            })
        except Exception as e:
            results.append({"symbol": symbol, "status": "error", "error": str(e)})
    
    return {"role": "executor", "results": results}


@api_router.get("/decision/latest")
def api_latest_decision():
    result = get_last_pipeline_run()
    if result is None:
        raise HTTPException(status_code=404, detail="No pipeline runs captured yet")
    return result


@api_router.get("/strategist/{symbol}")
def get_strategist_insight(symbol: str):
    record = get_latest_strategist_insight(symbol)
    if not record:
        raise HTTPException(status_code=404, detail="No strategist insight found")
    return {
        "id": record.id,
        "symbol": record.symbol,
        "provider": record.provider,
        "model": record.model,
        "generated_at": record.generated_at.isoformat() if record.generated_at else None,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "overall_trend": record.overall_trend,
        "next_days_trend": record.next_days_trend,
        "next_24h_trend": record.next_24h_trend,
        "next_few_hours_trend": record.next_few_hours_trend,
        "confidence": record.confidence,
        "rationale": record.rationale,
        "raw_output": record.raw_output,
    }


@api_router.get("/tactician/{symbol}")
def get_active_tactician_plan(symbol: str):
    plan = get_active_plan(symbol)
    if not plan:
        raise HTTPException(status_code=404, detail="No active plan for symbol")
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
                "metadata": step.step_metadata,
            }
            for step in plan.plan_steps
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
            for log in plan.execution_logs[:10]
        ],
        "metadata": plan.plan_metadata,
    }


@api_router.get("/executor/logs/{plan_id}")
def get_executor_logs(plan_id: int, limit: int = 50):
    logs = get_execution_logs(plan_id=plan_id, limit=limit)
    if not logs:
        raise HTTPException(status_code=404, detail="No execution logs found")
    return {
        "plan_id": plan_id,
        "count": len(logs),
        "logs": [
            {
                "id": log.id,
                "step_id": log.step_id,
                "status": log.status,
                "message": log.message,
                "payload": log.payload,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
    }

@api_router.get("/status")
def status():
    s = get_settings()
    return {
        "mode": s.mode,
        "symbols": s.symbols,
        "ai_cadence_minutes": s.ai_cadence_minutes,
        "price_spike_trigger_pct": s.price_spike_trigger_pct,
    }

@api_router.get("/export/candles/{symbol}/{timeframe}")
def export_candles(symbol: str, timeframe: str, limit: int = 1000, format: str = "json"):
    """
    Export candle data for a symbol and timeframe.
    
    Args:
        symbol: Trading pair (e.g., BTC-USDT)
        timeframe: 1m, 15m, 1H, 4H, or 1D
        limit: Max number of candles (default 1000)
        format: 'json' or 'csv'
    """
    candles = get_candles(symbol, timeframe, limit=limit)
    
    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume", "created_at"])
        for c in reversed(candles):  # oldest first
            writer.writerow([c.timestamp, c.open, c.high, c.low, c.close, c.volume, c.created_at])
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={symbol}_{timeframe}_candles.csv"}
        )
    else:
        data = [
            {
                "timestamp": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in reversed(candles)
        ]

@api_router.get("/export/decisions")
def export_decisions(
    limit: int = 100, 
    format: str = "json", 
    page: int | None = None, 
    page_size: int | None = None,
    role: str | None = None,
    symbol: str | None = None
):
    """
    Export AI decisions with optional filters.
    
    Args:
        limit: Max number of decisions (default 100)
        format: 'json' or 'csv'
        page: Page number (1-indexed)
        page_size: Items per page
        role: Filter by agent role (strategist, tactician, executor)
        symbol: Filter by symbol (e.g., BTC-USDT)
    """
    # CSV export sticks with simple latest-N semantics
    if format == "csv":
        decisions = get_recent_decisions(limit=limit)
    else:
        # JSON supports pagination; fall back to `limit` if no page params
        if page is not None and page_size is not None and page > 0 and page_size > 0:
            offset = (page - 1) * page_size
            decisions = get_decisions_page(offset=offset, limit=page_size, role=role, symbol=symbol)
        else:
            decisions = get_recent_decisions(limit=limit)
    
    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "timestamp", "provider", "confidence", "rationale", "next_decision_in_minutes"])
        for d in reversed(decisions):
            writer.writerow([d.id, d.timestamp, d.provider, d.confidence, d.rationale, d.next_decision_in_minutes])
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=decisions.csv"}
        )
    else:
        # Already ordered desc in query
        data = [
            {
                "id": d.id,
                "timestamp": d.timestamp.isoformat() if d.timestamp else None,
                "timestamp_ms": int(d.timestamp.timestamp() * 1000) if d.timestamp else None,
                "provider": d.provider,
                "confidence": d.confidence,
                "rationale": d.rationale,
                "next_decision_in_minutes": d.next_decision_in_minutes,
                "ai_input": d.ai_input,
                "ai_output": d.ai_output,
            }
            for d in decisions  # Already reversed from query
        ]
        total = count_decisions()
        resp: Dict[str, Any] = {"count": len(data), "data": data, "total": total}
        if page is not None and page_size is not None:
            resp.update({"page": page, "page_size": page_size, "total_pages": (total + page_size - 1) // page_size})
        return resp


@api_router.get("/holdings")
def get_holdings():
    """Return current exchange balances/positions valued in USDT (no strategy ledger)."""
    from ..config import get_settings
    s = get_settings()
    symbols = s.symbols
    # Get last prices and 24h change for valuation/display
    tickers = get_tickers_map(symbols)
    last_prices = {k: v.get("last", 0.0) for k, v in tickers.items()}

    # Exchange balances/positions
    exchange = {"positions": [], "total_value_usdt": 0.0}
    try:
        client = get_trading_client()
        bal = client.get_account_balance()
        if bal.get("code") == "0" and bal.get("data"):
            details = bal["data"][0].get("details", [])
            for d in details:
                ccy = d.get("ccy")
                avail = float(d.get("availBal", 0) or 0.0)
                cash_bal = float(d.get("cashBal", 0) or 0.0)
                equity_total = float(d.get("eq", 0) or 0.0)
                amount = max(avail, cash_bal, equity_total)

                if amount <= 0 and avail <= 0:
                    continue

                if ccy == "USDT":
                    exchange["positions"].append({
                        "symbol": "USDT",
                        "amount": amount,
                        "available": avail,
                        "value_usdt": amount,
                    })
                    exchange["total_value_usdt"] += amount
                else:
                    sym = f"{ccy}-USDT"
                    # If the symbol is not in initial symbols list, fetch its ticker inline
                    if sym not in last_prices:
                        extra = get_tickers_map([sym])
                        if sym in extra:
                            tickers[sym] = extra[sym]
                            last_prices[sym] = extra[sym].get("last", 0.0)
                    price = last_prices.get(sym, 0.0)
                    value = amount * price
                    exchange["positions"].append({
                        "symbol": sym,
                        "amount": amount,
                        "available": avail,
                        "last_price": price,
                        "change_24h_pct": tickers.get(sym, {}).get("change_24h_pct", 0.0),
                        "value_usdt": value,
                    })
                    exchange["total_value_usdt"] += value
    except Exception as e:
        exchange["error"] = str(e)

    return {
        "mode": s.mode,
        "exchange": exchange,
        "last_prices": last_prices,
        "tickers": tickers,
    }


@api_router.get("/equity")
def get_equity(limit: int = 200, format: str = "json"):
    """Return recent equity snapshots (total estimated account value in USDT)."""
    snaps = get_equity_snapshots(limit=limit)
    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["timestamp", "mode", "total_value_usdt", "source"])
        for s in reversed(snaps):
            writer.writerow([
                s.timestamp.isoformat() if s.timestamp else None,
                s.mode,
                s.total_value_usdt,
                s.source,
            ])
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=equity_snapshots.csv"}
        )
    else:
        data = [
            {
                "timestamp": s.timestamp.isoformat() if s.timestamp else None,
                "timestamp_ms": int(s.timestamp.timestamp() * 1000) if s.timestamp else None,
                "mode": s.mode,
                "total_value_usdt": s.total_value_usdt,
                "source": s.source,
            }
            for s in snaps
        ]
        return {"count": len(data), "data": data}


@api_router.post("/equity/snapshot")
def create_equity_snapshot():
    """Create an equity snapshot now using OKX asset valuation with fallback to computed holdings."""
    from ..config import get_settings
    s = get_settings()
    mode = s.mode
    total = 0.0
    source = "okx"
    details: Dict[str, Any] = {}
    try:
        client = get_trading_client()
        val = client.get_asset_valuation(ccy="USDT")
        if val.get("code") == "0" and val.get("data"):
            total = float(val["data"][0].get("totalBal", 0.0) or 0.0)
            details = val
        else:
            raise ValueError("Unexpected OKX valuation response")
    except Exception as e:
        # Fallback: compute from balances and last prices similar to /holdings
        source = "computed"
        try:
            tickers = get_tickers_map(s.symbols)
            last_prices = {k: v.get("last", 0.0) for k, v in tickers.items()}
            client = get_trading_client()
            bal = client.get_account_balance()
            total = 0.0
            if bal.get("code") == "0" and bal.get("data"):
                details = {"positions": []}
                for d in bal["data"][0].get("details", []):
                    ccy = d.get("ccy")
                    avail = float(d.get("availBal", 0) or 0)
                    if avail <= 0:
                        continue
                    if ccy == "USDT":
                        total += avail
                        details["positions"].append({"symbol": "USDT", "amount": avail, "value_usdt": avail})
                    else:
                        sym = f"{ccy}-USDT"
                        if sym not in last_prices:
                            extra = get_tickers_map([sym])
                            last_prices[sym] = extra.get(sym, {}).get("last", 0.0)
                        price = last_prices.get(sym, 0.0)
                        total += avail * price
                        details["positions"].append({"symbol": sym, "amount": avail, "last_price": price, "value_usdt": avail * price})
        except Exception as e2:
            raise HTTPException(status_code=500, detail=f"Failed to snapshot equity: {e2}")

    try:
        save_equity_snapshot(mode=mode, total_value_usdt=total, source=source, details=details)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save equity snapshot: {e}")

    return {"mode": mode, "total_value_usdt": total, "source": source}


@api_router.get("/equity/changes")
def get_equity_changes():
    """
    Return equity change statistics:
    - Current value
    - Value 24h ago
    - Change in last 24h (absolute and percentage)
    - First recorded value (beginning)
    - Total change from beginning (absolute and percentage)
    """
    from datetime import datetime, timedelta, timezone
    
    # Get most recent snapshot (current)
    current_snaps = get_equity_snapshots(limit=1)
    if not current_snaps:
        raise HTTPException(status_code=404, detail="No equity snapshots found. Create one with POST /api/equity/snapshot")
    
    current = current_snaps[0]
    current_value = current.total_value_usdt
    current_time = current.timestamp
    
    # Get all snapshots to find 24h ago and first ever
    all_snaps = get_equity_snapshots(limit=10000)  # Get all snapshots
    
    if not all_snaps:
        raise HTTPException(status_code=404, detail="No equity snapshots found")
    
    # Find snapshot ~24h ago (closest to 24h before current)
    target_24h_ago = current_time - timedelta(hours=24)
    snap_24h_ago = None
    min_diff = None
    
    for snap in reversed(all_snaps):  # Reversed to go chronologically
        if snap.timestamp <= target_24h_ago:
            diff = abs((target_24h_ago - snap.timestamp).total_seconds())
            if min_diff is None or diff < min_diff:
                min_diff = diff
                snap_24h_ago = snap
    
    # Calculate 24h changes
    if snap_24h_ago:
        value_24h_ago = snap_24h_ago.total_value_usdt
        change_24h = current_value - value_24h_ago
        change_24h_pct = (change_24h / value_24h_ago * 100) if value_24h_ago > 0 else 0
        time_24h_ago = snap_24h_ago.timestamp
    else:
        value_24h_ago = None
        change_24h = None
        change_24h_pct = None
        time_24h_ago = None
    
    # Get first snapshot (beginning)
    first_snap = all_snaps[-1]  # Last in list = oldest
    first_value = first_snap.total_value_usdt
    first_time = first_snap.timestamp
    
    # Calculate total change from beginning
    change_total = current_value - first_value
    change_total_pct = (change_total / first_value * 100) if first_value > 0 else 0
    
    return {
        "current": {
            "timestamp": current_time.isoformat() if current_time else None,
            "value_usdt": round(current_value, 2),
            "source": current.source,
            "mode": current.mode,
        },
        "change_24h": {
            "timestamp": time_24h_ago.isoformat() if time_24h_ago else None,
            "value_usdt": round(value_24h_ago, 2) if value_24h_ago is not None else None,
            "change_usdt": round(change_24h, 2) if change_24h is not None else None,
            "change_pct": round(change_24h_pct, 2) if change_24h_pct is not None else None,
            "available": snap_24h_ago is not None,
        },
        "change_from_beginning": {
            "timestamp": first_time.isoformat() if first_time else None,
            "value_usdt": round(first_value, 2),
            "change_usdt": round(change_total, 2),
            "change_pct": round(change_total_pct, 2),
            "duration_hours": round((current_time - first_time).total_seconds() / 3600, 1) if current_time and first_time else None,
        },
        "snapshot_count": len(all_snaps),
    }

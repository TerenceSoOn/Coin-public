from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy.orm import selectinload, joinedload

from .database import SessionLocal
from .models import (
    Candle,
    Decision,
    Trade,
    EquitySnapshot,
    StrategistInsight,
    TacticianPlan,
    PlanStep,
    PlanExecutionLog,
    PredictorInsight,
    TraderDecision,
    TraderMemory,
)


def save_candles(symbol: str, timeframe: str, candle_data: List[List[str]]) -> None:
    """
    Save candle data to database.
    
    Args:
        symbol: Trading pair (e.g., 'BTC-USDT')
        timeframe: Timeframe (e.g., '1m', '15m', '1H', '4H', '1D')
        candle_data: List of candles from OKX API
    """
    db = SessionLocal()
    try:
        for row in candle_data:
            timestamp = int(row[0])
            # Check if already exists
            existing = db.query(Candle).filter(
                Candle.symbol == symbol,
                Candle.timeframe == timeframe,
                Candle.timestamp == timestamp
            ).first()
            if existing:
                continue
            
            candle = Candle(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=timestamp,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            db.add(candle)
        db.commit()
    finally:
        db.close()


def get_candles(symbol: str, timeframe: str, limit: int = 100) -> List[Candle]:
    """Get recent candles from database."""
    db = SessionLocal()
    try:
        return db.query(Candle).filter(
            Candle.symbol == symbol,
            Candle.timeframe == timeframe
        ).order_by(Candle.timestamp.desc()).limit(limit).all()
    finally:
        db.close()


def save_decision(
    provider: str,
    ai_input: Dict[str, Any],
    ai_output: Dict[str, Any],
    execution_results: List[Dict[str, Any]] | None = None,
    *,
    role: str = "legacy",
) -> None:
    """
    Save an AI decision to database.
    
    Args:
        provider: AI provider name ('deepseek', 'openai', etc.')
        ai_input: Full input payload sent to AI
        ai_output: Full output from AI
        execution_results: Trade execution results (optional)
    """
    db = SessionLocal()
    try:
        decision = Decision(
            provider=provider,
            role=role,
            ai_input=ai_input,
            ai_output=ai_output,
            execution_results=execution_results,
            rationale=ai_output.get("rationale"),
            confidence=ai_output.get("confidence"),
            next_decision_in_minutes=ai_output.get("next_decision_in_minutes"),
        )
        db.add(decision)
        db.commit()
    finally:
        db.close()


def get_recent_decisions(limit: int = 10) -> List[Decision]:
    """Get recent decisions from database (legacy helper, latest N)."""
    db = SessionLocal()
    try:
        return db.query(Decision).order_by(Decision.timestamp.desc()).limit(limit).all()
    finally:
        db.close()


def count_decisions() -> int:
    """Return total number of decisions for pagination."""
    db = SessionLocal()
    try:
        return db.query(Decision).count()
    finally:
        db.close()


def get_decisions_page(offset: int, limit: int, role: str | None = None, symbol: str | None = None) -> List[Decision]:
    """Get a page of decisions ordered by timestamp desc with optional filters.

    Args:
        offset: Number of rows to skip
        limit: Page size
        role: Filter by role (strategist, tactician, executor, etc.)
        symbol: Filter by symbol in ai_input JSON
    """
    db = SessionLocal()
    try:
        query = db.query(Decision).order_by(Decision.timestamp.desc())
        
        # Filter by role if provided
        if role:
            query = query.filter(Decision.role == role)
        
        # Filter by symbol if provided (check if symbol exists in ai_input.symbols array)
        if symbol:
            query = query.filter(Decision.ai_input['symbols'].astext.contains(symbol))
        
        return query.offset(offset).limit(limit).all()
    finally:
        db.close()


def save_strategist_insight(
    *,
    symbol: str,
    provider: str,
    model: str | None,
    overall_trend: str,
    next_days_trend: str,
    next_24h_trend: str,
    next_few_hours_trend: str,
    confidence: float | None,
    rationale: str | None,
    raw_output: Dict[str, Any],
    context_snapshot: Dict[str, Any] | None,
    expires_at: datetime | None,
) -> StrategistInsight:
    db = SessionLocal()
    try:
        insight = StrategistInsight(
            symbol=symbol,
            provider=provider,
            model=model,
            overall_trend=overall_trend,
            next_days_trend=next_days_trend,
            next_24h_trend=next_24h_trend,
            next_few_hours_trend=next_few_hours_trend,
            confidence=confidence,
            rationale=rationale,
            raw_output=raw_output,
            context_snapshot=context_snapshot,
            expires_at=expires_at,
        )
        db.add(insight)
        db.commit()
        db.refresh(insight)
        return insight
    finally:
        db.close()


def get_latest_strategist_insight(symbol: str) -> StrategistInsight | None:
    db = SessionLocal()
    try:
        return (
            db.query(StrategistInsight)
            .filter(StrategistInsight.symbol == symbol)
            .order_by(StrategistInsight.generated_at.desc())
            .first()
        )
    finally:
        db.close()


def save_tactician_plan(
    *,
    symbol: str,
    strategist_insight_id: int,
    provider: str,
    model: str | None,
    status: str,
    confidence: float | None,
    rationale: str | None,
    raw_output: Dict[str, Any],
    plan_metadata: Dict[str, Any] | None,
    expires_at: datetime | None,
    next_eval_at: datetime | None,
    steps: List[Dict[str, Any]],
) -> TacticianPlan:
    db = SessionLocal()
    try:
        plan = TacticianPlan(
            symbol=symbol,
            strategist_insight_id=strategist_insight_id,
            provider=provider,
            model=model,
            status=status,
            confidence=confidence,
            rationale=rationale,
            raw_output=raw_output,
            plan_metadata=plan_metadata,
            expires_at=expires_at,
            next_eval_at=next_eval_at,
        )
        db.add(plan)
        db.flush()

        for index, step in enumerate(steps):
            payload = dict(step or {})
            db.add(
                PlanStep(
                    plan_id=plan.id,
                    sequence_index=payload.get("sequence_index", index),
                    action=payload.get("action", "enter"),
                    side=payload.get("side"),
                    entry_price=payload.get("entry_price"),
                    exit_price=payload.get("exit_price"),
                    stop_loss_price=payload.get("stop_loss_price"),
                    take_profit_price=payload.get("take_profit_price"),
                    size_usd=payload.get("size_usd"),
                    size_pct=payload.get("size_pct"),
                    leverage=payload.get("leverage"),
                    status=payload.get("status", "pending"),
                    step_metadata=payload.get("metadata"),
                )
            )

        db.commit()
        db.refresh(plan)
        plan.plan_steps  # eager load
        plan.execution_logs  # eager load (even if empty)
        return plan
    finally:
        db.close()


def get_active_plan(symbol: str) -> TacticianPlan | None:
    db = SessionLocal()
    try:
        plan = (
            db.query(TacticianPlan)
            .options(
                joinedload(TacticianPlan.plan_steps),
                joinedload(TacticianPlan.execution_logs),
            )
            .filter(TacticianPlan.symbol == symbol)
            .filter(TacticianPlan.status.in_(["ready", "hold", "in_progress"]))
            .order_by(TacticianPlan.created_at.desc())
            .first()
        )
        # Materialize relationships before expunging
        if plan:
            # Force load the relationships
            _ = list(plan.plan_steps)
            _ = list(plan.execution_logs)
            # Now expunge to detach from session
            db.expunge(plan)
        return plan
    finally:
        db.close()


def update_plan_status(
    plan_id: int,
    status: str,
    *,
    next_eval_at: datetime | None = None,
    metadata: Dict[str, Any] | None = None,
) -> None:
    db = SessionLocal()
    try:
        plan = db.query(TacticianPlan).filter(TacticianPlan.id == plan_id).one_or_none()
        if not plan:
            return
        plan.status = status
        plan.next_eval_at = next_eval_at
        if metadata:
            existing = plan.plan_metadata or {}
            existing.update(metadata)
            plan.plan_metadata = existing
        db.commit()
    finally:
        db.close()


def get_plan_steps(plan_id: int) -> List[PlanStep]:
    db = SessionLocal()
    try:
        return (
            db.query(PlanStep)
            .filter(PlanStep.plan_id == plan_id)
            .order_by(PlanStep.sequence_index.asc())
            .all()
        )
    finally:
        db.close()


def update_plan_step_status(
    step_id: int,
    status: str,
    *,
    executed_at: datetime | None = None,
    metadata: Dict[str, Any] | None = None,
) -> None:
    db = SessionLocal()
    try:
        step = db.query(PlanStep).filter(PlanStep.id == step_id).one_or_none()
        if not step:
            return
        step.status = status
        step.executed_at = executed_at
        if metadata:
            existing = step.step_metadata or {}
            existing.update(metadata)
            step.step_metadata = existing
        db.commit()
    finally:
        db.close()


def append_execution_log(
    *,
    plan_id: int,
    step_id: int | None,
    provider: str,
    model: str | None,
    status: str,
    message: str | None,
    payload: Dict[str, Any] | None,
) -> PlanExecutionLog:
    db = SessionLocal()
    try:
        log = PlanExecutionLog(
            plan_id=plan_id,
            step_id=step_id,
            provider=provider,
            model=model,
            status=status,
            message=message,
            payload=payload,
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log
    finally:
        db.close()


def get_execution_logs(plan_id: int, limit: int = 50) -> List[PlanExecutionLog]:
    db = SessionLocal()
    try:
        return (
            db.query(PlanExecutionLog)
            .filter(PlanExecutionLog.plan_id == plan_id)
            .order_by(PlanExecutionLog.created_at.desc())
            .limit(limit)
            .all()
        )
    finally:
        db.close()


def save_trade(**kwargs) -> None:
    """Persist a trade record to the database."""
    db = SessionLocal()
    try:
        trade = Trade(**kwargs)
        db.add(trade)
        db.commit()
    finally:
        db.close()


def get_trades(symbol: str | None = None, mode: str | None = None) -> List[Trade]:
    db = SessionLocal()
    try:
        q = db.query(Trade).order_by(Trade.timestamp.desc())
        if symbol:
            q = q.filter(Trade.symbol == symbol)
        if mode:
            q = q.filter(Trade.mode == mode)
        return q.all()
    finally:
        db.close()


def compute_holdings_from_trades(trades: List[Trade], last_prices: Dict[str, float]) -> Dict[str, Dict[str, Any]]:
    """Compute holdings and P&L by symbol using weighted average cost.

    Returns dict[symbol] = {
        amount, avg_cost, cost_usdt, value_usdt, pnl_abs, pnl_pct
    }
    """
    holdings: Dict[str, Dict[str, float]] = {}
    for t in reversed(trades):  # oldest to newest
        sym = t.symbol
        rec = holdings.setdefault(sym, {"amount": 0.0, "cost_usdt": 0.0})
        if t.side == "buy":
            # Increase amount and cost
            rec["cost_usdt"] += t.base_amount * t.price_usdt
            rec["amount"] += t.base_amount
        elif t.side == "sell":
            sell_amt = min(rec["amount"], t.base_amount)
            if rec["amount"] > 0:
                avg_cost = rec["cost_usdt"] / rec["amount"] if rec["amount"] else 0.0
            else:
                avg_cost = 0.0
            rec["amount"] -= sell_amt
            rec["cost_usdt"] -= sell_amt * avg_cost
            # If sell exceeds amount, ignore extra (no short management here)
        else:
            continue

    # Finalize with valuations
    result: Dict[str, Dict[str, Any]] = {}
    for sym, rec in holdings.items():
        amt = rec["amount"]
        cost = rec["cost_usdt"]
        avg_cost = (cost / amt) if amt > 0 else 0.0
        last = last_prices.get(sym, 0.0)
        value = amt * last
        pnl_abs = value - cost
        pnl_pct = (pnl_abs / cost) if cost > 0 else 0.0
        result[sym] = {
            "amount": amt,
            "avg_cost": avg_cost,
            "cost_usdt": cost,
            "last_price": last,
            "value_usdt": value,
            "pnl_abs": pnl_abs,
            "pnl_pct": pnl_pct,
        }
    return result


def save_equity_snapshot(mode: str, total_value_usdt: float, source: str = "computed", details: Dict[str, Any] | None = None) -> None:
    """Persist a snapshot of account equity in USDT."""
    db = SessionLocal()
    try:
        snap = EquitySnapshot(mode=mode, total_value_usdt=total_value_usdt, source=source, details=details or {})
        db.add(snap)
        db.commit()
    finally:
        db.close()


def get_equity_snapshots(limit: int = 200) -> List[EquitySnapshot]:
    """Return recent equity snapshots ordered by timestamp desc."""
    db = SessionLocal()
    try:
        return db.query(EquitySnapshot).order_by(EquitySnapshot.timestamp.desc()).limit(limit).all()
    finally:
        db.close()


# ============================================================================
# V2 Agent Repository Functions (Predictor + Trader)
# ============================================================================

def save_predictor_insight(
    *,
    symbol: str,
    provider: str,
    model: str | None,
    overall_trend: str,
    trend_strength: float | None,
    next_24h_bias: str,
    next_4h_bias: str,
    support_levels: List[float] | None,
    resistance_levels: List[float] | None,
    confidence: float | None,
    reasoning: str | None,
    news_context: str | None,
    raw_output: Dict[str, Any],
    context_snapshot: Dict[str, Any] | None,
    expires_at: datetime | None,
) -> PredictorInsight:
    """Save a Predictor insight to the database."""
    db = SessionLocal()
    try:
        insight = PredictorInsight(
            symbol=symbol,
            provider=provider,
            model=model,
            overall_trend=overall_trend,
            trend_strength=trend_strength,
            next_24h_bias=next_24h_bias,
            next_4h_bias=next_4h_bias,
            support_levels=support_levels or [],
            resistance_levels=resistance_levels or [],
            confidence=confidence,
            reasoning=reasoning,
            news_context=news_context,
            raw_output=raw_output,
            context_snapshot=context_snapshot,
            expires_at=expires_at,
        )
        db.add(insight)
        db.commit()
        db.refresh(insight)
        return insight
    finally:
        db.close()


def get_latest_predictor_insight(symbol: str) -> PredictorInsight | None:
    """Get the most recent Predictor insight for a symbol."""
    db = SessionLocal()
    try:
        return (
            db.query(PredictorInsight)
            .filter(PredictorInsight.symbol == symbol)
            .order_by(PredictorInsight.generated_at.desc())
            .first()
        )
    finally:
        db.close()


def save_trader_decision(
    *,
    symbol: str,
    predictor_insight_id: int,
    provider: str,
    model: str | None,
    action: str,
    side: str | None,
    entry_price: float | None,
    size_usd: float | None,
    leverage: int,
    stop_loss: float | None,
    take_profit: float | None,
    confidence: float | None,
    reason: str | None,
    invalidation_condition: str | None,
    memory_summary: str | None,
    status: str,
    raw_output: Dict[str, Any],
    context_snapshot: Dict[str, Any] | None,
) -> TraderDecision:
    """Save a Trader decision to the database."""
    db = SessionLocal()
    try:
        decision = TraderDecision(
            symbol=symbol,
            predictor_insight_id=predictor_insight_id,
            provider=provider,
            model=model,
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
            status=status,
            raw_output=raw_output,
            context_snapshot=context_snapshot,
        )
        db.add(decision)
        db.commit()
        db.refresh(decision)
        return decision
    finally:
        db.close()


def get_latest_trader_decision(symbol: str) -> TraderDecision | None:
    """Get the most recent Trader decision for a symbol."""
    db = SessionLocal()
    try:
        return (
            db.query(TraderDecision)
            .filter(TraderDecision.symbol == symbol)
            .order_by(TraderDecision.created_at.desc())
            .first()
        )
    finally:
        db.close()


def update_trader_decision_execution(
    decision_id: int,
    status: str,
    execution_result: Dict[str, Any],
) -> None:
    """Update Trader decision with execution result."""
    db = SessionLocal()
    try:
        decision = db.query(TraderDecision).filter(TraderDecision.id == decision_id).one_or_none()
        if decision:
            decision.status = status
            decision.execution_result = execution_result
            decision.executed_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


def save_trader_memory(symbol: str, memory_summary: str, decision_id: int | None = None) -> TraderMemory:
    """Save Trader memory for a symbol."""
    db = SessionLocal()
    try:
        memory = TraderMemory(
            symbol=symbol,
            memory_summary=memory_summary,
            decision_id=decision_id,
        )
        db.add(memory)
        db.commit()
        db.refresh(memory)
        return memory
    finally:
        db.close()


def get_latest_trader_memory(symbol: str) -> TraderMemory | None:
    """Get the most recent Trader memory for a symbol."""
    db = SessionLocal()
    try:
        return (
            db.query(TraderMemory)
            .filter(TraderMemory.symbol == symbol)
            .order_by(TraderMemory.created_at.desc())
            .first()
        )
    finally:
        db.close()


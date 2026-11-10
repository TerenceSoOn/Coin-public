from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    BigInteger,
    Text,
    DateTime,
    JSON,
    Index,
    ForeignKey,
)
from sqlalchemy.orm import relationship

from .database import Base


class Candle(Base):
    """Multi-timeframe OHLCV candle data from OKX."""

    __tablename__ = "candles"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    timeframe = Column(String(10), nullable=False, index=True)
    timestamp = Column(BigInteger, nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_candles_symbol_timeframe_timestamp", "symbol", "timeframe", "timestamp"),
    )

    def __repr__(self) -> str:
        return f"<Candle {self.symbol} {self.timeframe} @ {self.timestamp}>"


class Decision(Base):
    """AI decision records with full input/output.

    `role` tracks which pipeline stage produced the decision (strategist, tactician, executor, legacy).
    """

    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    provider = Column(String(50), nullable=False)
    role = Column(String(20), nullable=False, default="legacy")
    ai_input = Column(JSON, nullable=False)
    ai_output = Column(JSON, nullable=False)
    execution_results = Column(JSON, nullable=True)
    rationale = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    next_decision_in_minutes = Column(Integer, nullable=True)

    def __repr__(self) -> str:
        return f"<Decision {self.id} role={self.role} provider={self.provider} @ {self.timestamp}>"


class Trade(Base):
    """Executed or simulated trades for cost-basis and P&L tracking."""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    mode = Column(String(20), nullable=False)
    provider = Column(String(50), nullable=True)
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)
    base_amount = Column(Float, nullable=False)
    price_usdt = Column(Float, nullable=False)
    notional_usdt = Column(Float, nullable=False)
    leverage = Column(Integer, nullable=True)
    order_id = Column(String(100), nullable=True)
    status = Column(String(20), nullable=False, default="executed")

    def __repr__(self) -> str:
        return f"<Trade {self.symbol} {self.side} {self.base_amount} @ {self.price_usdt}>"


class EquitySnapshot(Base):
    """Periodic snapshot of total estimated account equity in USDT."""

    __tablename__ = "equity_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    mode = Column(String(20), nullable=False)
    total_value_usdt = Column(Float, nullable=False)
    source = Column(String(20), nullable=True)
    details = Column(JSON, nullable=True)

    def __repr__(self) -> str:
        return f"<EquitySnapshot {self.mode} ${self.total_value_usdt:.2f} @ {self.timestamp}>"


class StrategistInsight(Base):
    """Strategist role output cached per symbol to drive downstream planning."""

    __tablename__ = "strategist_insights"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    provider = Column(String(50), nullable=False)
    model = Column(String(100), nullable=True)
    generated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    expires_at = Column(DateTime, nullable=True)
    overall_trend = Column(String(50), nullable=False)
    next_days_trend = Column(String(50), nullable=False)
    next_24h_trend = Column(String(50), nullable=False)
    next_few_hours_trend = Column(String(50), nullable=False)
    confidence = Column(Float, nullable=True)
    rationale = Column(Text, nullable=True)
    raw_output = Column(JSON, nullable=False)
    context_snapshot = Column(JSON, nullable=True)

    plans = relationship(
        "TacticianPlan",
        back_populates="strategist_insight",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<StrategistInsight {self.symbol} {self.overall_trend} @ {self.generated_at}>"


class TacticianPlan(Base):
    """Tactician plan describing profit opportunities and required steps."""

    __tablename__ = "tactician_plans"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    strategist_insight_id = Column(Integer, ForeignKey("strategist_insights.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String(50), nullable=False)
    model = Column(String(100), nullable=True)
    status = Column(String(20), nullable=False, default="ready")
    confidence = Column(Float, nullable=True)
    rationale = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=True)
    next_eval_at = Column(DateTime, nullable=True)
    raw_output = Column(JSON, nullable=False)
    plan_metadata = Column(JSON, nullable=True)

    strategist_insight = relationship("StrategistInsight", back_populates="plans")
    plan_steps = relationship(
        "PlanStep",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="PlanStep.sequence_index",
    )
    execution_logs = relationship(
        "PlanExecutionLog",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="PlanExecutionLog.created_at.desc()",
    )

    def __repr__(self) -> str:
        return f"<TacticianPlan {self.symbol} status={self.status} conf={self.confidence}>"


class PlanStep(Base):
    """Normalized plan steps used by executor to place orders sequentially."""

    __tablename__ = "plan_steps"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("tactician_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence_index = Column(Integer, nullable=False)
    action = Column(String(20), nullable=False)
    side = Column(String(10), nullable=True)
    entry_price = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    stop_loss_price = Column(Float, nullable=True)
    take_profit_price = Column(Float, nullable=True)
    size_usd = Column(Float, nullable=True)
    size_pct = Column(Float, nullable=True)
    leverage = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    executed_at = Column(DateTime, nullable=True)
    step_metadata = Column(JSON, nullable=True)

    plan = relationship("TacticianPlan", back_populates="plan_steps")

    __table_args__ = (
        Index("ix_plan_steps_plan_seq", "plan_id", "sequence_index"),
    )

    def __repr__(self) -> str:
        return f"<PlanStep plan={self.plan_id} seq={self.sequence_index} action={self.action} status={self.status}>"


class PlanExecutionLog(Base):
    """Executor AI memory of past actions and deviations for a plan."""

    __tablename__ = "plan_execution_logs"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("tactician_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    step_id = Column(Integer, ForeignKey("plan_steps.id", ondelete="SET NULL"), nullable=True, index=True)
    provider = Column(String(50), nullable=False)
    model = Column(String(100), nullable=True)
    status = Column(String(20), nullable=False)
    message = Column(Text, nullable=True)
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    plan = relationship("TacticianPlan", back_populates="execution_logs")

    def __repr__(self) -> str:
        return f"<PlanExecutionLog plan={self.plan_id} status={self.status} @ {self.created_at}>"


# ============================================================================
# V2 Agent Models (Predictor + Trader)
# ============================================================================

class PredictorInsight(Base):
    """Predictor agent output: macro trend analysis from 1D/4H data."""

    __tablename__ = "predictor_insights"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    provider = Column(String(50), nullable=False)
    model = Column(String(100), nullable=True)
    generated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    expires_at = Column(DateTime, nullable=True)
    
    # Trend predictions
    overall_trend = Column(String(50), nullable=False)  # bullish|bearish|neutral
    trend_strength = Column(Float, nullable=True)  # 0.0-1.0
    next_24h_bias = Column(String(50), nullable=False)  # up|down|sideways
    next_4h_bias = Column(String(50), nullable=False)  # up|down|sideways
    
    # Key levels
    support_levels = Column(JSON, nullable=True)  # [price1, price2, ...]
    resistance_levels = Column(JSON, nullable=True)  # [price1, price2, ...]
    
    # Metadata
    confidence = Column(Float, nullable=True)
    reasoning = Column(Text, nullable=True)
    news_context = Column(Text, nullable=True)  # Brief summary of relevant news
    raw_output = Column(JSON, nullable=False)
    context_snapshot = Column(JSON, nullable=True)

    # Relationships
    trader_decisions = relationship(
        "TraderDecision",
        back_populates="predictor_insight",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<PredictorInsight {self.symbol} {self.overall_trend} strength={self.trend_strength} @ {self.generated_at}>"


class TraderDecision(Base):
    """Trader agent decision: execution-ready trade with memory."""

    __tablename__ = "trader_decisions"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    predictor_insight_id = Column(Integer, ForeignKey("predictor_insights.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String(50), nullable=False)
    model = Column(String(100), nullable=True)
    
    # Decision output
    action = Column(String(20), nullable=False)  # buy|sell|hold
    side = Column(String(10), nullable=True)  # long|short
    entry_price = Column(Float, nullable=True)  # null = market order
    size_usd = Column(Float, nullable=True)
    leverage = Column(Integer, default=1)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    
    # Context
    confidence = Column(Float, nullable=True)
    reason = Column(Text, nullable=True)
    invalidation_condition = Column(Text, nullable=True)  # Exit condition
    memory_summary = Column(Text, nullable=True)  # Memory for next call
    
    # Execution tracking
    status = Column(String(20), nullable=False, default="pending")  # pending|executed|failed
    executed_at = Column(DateTime, nullable=True)
    execution_result = Column(JSON, nullable=True)  # OKX order response
    
    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Raw data
    raw_output = Column(JSON, nullable=False)
    context_snapshot = Column(JSON, nullable=True)

    # Relationships
    predictor_insight = relationship("PredictorInsight", back_populates="trader_decisions")

    def __repr__(self) -> str:
        return f"<TraderDecision {self.symbol} {self.action} ${self.size_usd} conf={self.confidence} @ {self.created_at}>"


class TraderMemory(Base):
    """Trader agent memory: summary of recent actions per symbol."""

    __tablename__ = "trader_memory"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    memory_summary = Column(Text, nullable=False)  # Concise summary for AI context
    decision_id = Column(Integer, ForeignKey("trader_decisions.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    __table_args__ = (
        Index("ix_trader_memory_symbol_created", "symbol", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<TraderMemory {self.symbol} @ {self.created_at}>"

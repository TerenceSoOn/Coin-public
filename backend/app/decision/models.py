from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class MarketTimeframe:
	timeframe: str
	bars: List[Dict[str, float]]
	change_pct: float
	support: float | None
	resistance: float | None


@dataclass(slots=True)
class MarketSnapshot:
	symbol: str
	last_price: float
	change_1m_pct: float
	timeframes: Dict[str, MarketTimeframe]


@dataclass(slots=True)
class MarketEnvelope:
	retrieved_at: datetime
	snapshots: Dict[str, MarketSnapshot]


@dataclass(slots=True)
class AccountPosition:
	symbol: str
	side: str
	amount: float
	value_usdt: float


@dataclass(slots=True)
class AccountState:
	balances: Dict[str, float]
	available_balances: Dict[str, float]
	positions: Dict[str, AccountPosition]
	recent_trades: List[Dict[str, Any]] = field(default_factory=list)
	recent_decisions: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class StrategistContextData:
	symbols: List[str]
	market: MarketEnvelope
	account: AccountState
	collected_at: datetime


@dataclass(slots=True)
class StrategistInsightData:
	id: Optional[int] = None
	symbol: str = ""
	provider: str = ""
	model: Optional[str] = None
	generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
	overall_trend: str = "unknown"
	next_days_trend: str = "unknown"
	next_24h_trend: str = "unknown"
	next_few_hours_trend: str = "unknown"
	confidence: Optional[float] = None
	rationale: Optional[str] = None
	expires_at: Optional[datetime] = None
	raw_output: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PlanStepData:
	id: Optional[int] = None
	sequence_index: int = 0
	action: str = "hold"
	side: Optional[str] = None
	entry_price: Optional[float] = None
	exit_price: Optional[float] = None
	stop_loss_price: Optional[float] = None
	take_profit_price: Optional[float] = None
	size_usd: Optional[float] = None
	size_pct: Optional[float] = None
	leverage: Optional[int] = None
	status: str = "pending"
	executed_at: Optional[datetime] = None
	metadata: Dict[str, Any] | None = None


@dataclass(slots=True)
class PlanExecutionLogData:
	id: Optional[int]
	step_id: Optional[int]
	status: str
	message: Optional[str]
	payload: Dict[str, Any] | None
	created_at: Optional[datetime]


@dataclass(slots=True)
class TacticianPlanData:
	id: Optional[int] = None
	symbol: str = ""
	provider: str = ""
	model: Optional[str] = None
	strategist_insight_id: Optional[int] = None
	status: str = "hold"
	confidence: Optional[float] = None
	rationale: Optional[str] = None
	created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
	expires_at: Optional[datetime] = None
	next_eval_at: Optional[datetime] = None
	raw_output: Dict[str, Any] = field(default_factory=dict)
	metadata: Dict[str, Any] | None = None
	steps: List[PlanStepData] = field(default_factory=list)
	execution_logs: List[PlanExecutionLogData] = field(default_factory=list)


@dataclass(slots=True)
class ExecutorDecision:
	plan_id: int
	step_id: Optional[int]
	provider: str
	model: Optional[str]
	status: str
	orders: List[Dict[str, Any]]
	rationale: Optional[str]
	confidence: Optional[float]
	raw_output: Dict[str, Any]


# ============================================================================
# V2 Agent Data Models (Predictor + Trader)
# ============================================================================

@dataclass(slots=True)
class PredictorInsightData:
	"""Predictor agent output: macro trend analysis."""
	id: Optional[int] = None
	symbol: str = ""
	provider: str = ""
	model: Optional[str] = None
	generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
	expires_at: Optional[datetime] = None
	
	# Trend predictions
	overall_trend: str = "neutral"  # bullish|bearish|neutral
	trend_strength: Optional[float] = None  # 0.0-1.0
	next_24h_bias: str = "sideways"  # up|down|sideways
	next_4h_bias: str = "sideways"  # up|down|sideways
	
	# Key levels
	support_levels: List[float] = field(default_factory=list)
	resistance_levels: List[float] = field(default_factory=list)
	
	# Metadata
	confidence: Optional[float] = None
	reasoning: Optional[str] = None
	news_context: Optional[str] = None
	raw_output: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TraderDecisionData:
	"""Trader agent decision: execution-ready trade with memory."""
	id: Optional[int] = None
	symbol: str = ""
	predictor_insight_id: Optional[int] = None
	provider: str = ""
	model: Optional[str] = None
	
	# Decision output
	action: str = "hold"  # buy|sell|hold
	side: Optional[str] = None  # long|short
	entry_price: Optional[float] = None  # null = market order
	size_usd: Optional[float] = None
	leverage: int = 1
	stop_loss: Optional[float] = None
	take_profit: Optional[float] = None
	
	# Context
	confidence: Optional[float] = None
	reason: Optional[str] = None
	invalidation_condition: Optional[str] = None
	memory_summary: Optional[str] = None
	
	# Execution tracking
	status: str = "pending"  # pending|executed|failed
	executed_at: Optional[datetime] = None
	execution_result: Dict[str, Any] = field(default_factory=dict)
	
	# Timestamps
	created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
	updated_at: Optional[datetime] = None
	
	# Raw data
	raw_output: Dict[str, Any] = field(default_factory=dict)


from __future__ import annotations

from datetime import datetime, timezone, timedelta
from threading import Lock
from typing import Any, Dict, List, Optional

from ..config import get_settings
from ..db.repository import get_recent_decisions, get_trades
from ..okx.client import TIMEFRAMES, get_last_prices, get_multi_timeframe_candles
from ..okx.trading_client import get_trading_client
from .indicators import calculate_support_resistance
from .models import (
	AccountPosition,
	AccountState,
	MarketEnvelope,
	MarketSnapshot,
	MarketTimeframe,
	StrategistContextData,
)


class DataCollector:
	"""Pulls market, account, and history context shared across decision roles."""

	def __init__(self) -> None:
		self.settings = get_settings()
		self._cache_lock = Lock()
		self._strategist_cache: Dict[tuple[str, ...], StrategistContextData] = {}
		self._strategist_cache_expiry: Dict[tuple[str, ...], datetime] = {}

	def build_strategist_context(
		self,
		symbols: Optional[List[str]] = None,
		*,
		force_refresh: bool = False,
	) -> StrategistContextData:
		"""Return cached strategist context with optional refresh control."""
		self.settings = get_settings()
		target_symbols = symbols or self.settings.symbols
		target_symbols = list(dict.fromkeys(target_symbols))
		if not target_symbols:
			raise ValueError("At least one symbol is required for strategist context")
		sorted_key = tuple(sorted(set(target_symbols)))
		now = datetime.now(timezone.utc)
		ttl_minutes = getattr(self.settings, "strategist_cache_ttl_minutes", 240)
		with self._cache_lock:
			cached = self._strategist_cache.get(sorted_key)
			expires_at = self._strategist_cache_expiry.get(sorted_key)
			if not force_refresh and cached and expires_at and expires_at > now:
				return cached

		envelope = self.build_market_envelope(list(target_symbols))
		account_state = self.build_account_state(list(target_symbols))
		context = StrategistContextData(
			symbols=list(target_symbols),
			market=envelope,
			account=account_state,
			collected_at=envelope.retrieved_at,
		)
		expires_at = envelope.retrieved_at + timedelta(minutes=ttl_minutes)
		with self._cache_lock:
			self._strategist_cache[sorted_key] = context
			self._strategist_cache_expiry[sorted_key] = expires_at
		return context

	def build_market_envelope(self, symbols: List[str]) -> MarketEnvelope:
		self.settings = get_settings()
		retrieved_at = datetime.now(timezone.utc)
		try:
			last_prices = get_last_prices(symbols)
		except Exception as exc:  # pragma: no cover - network failures
			print(f"[DataCollector] Failed to fetch last prices: {exc}")
			last_prices = {sym: 0.0 for sym in symbols}

		snapshots: Dict[str, MarketSnapshot] = {}
		for symbol in symbols:
			try:
				timeframe_candles = get_multi_timeframe_candles(symbol)
			except Exception as exc:  # pragma: no cover - network failures
				print(f"[DataCollector] Failed to fetch candles for {symbol}: {exc}")
				timeframe_candles = {}

			change_1m_pct = self._calculate_recent_change(timeframe_candles.get("1m", []))
			timeframes: Dict[str, MarketTimeframe] = {}

			for tf in TIMEFRAMES:
				candles = timeframe_candles.get(tf, [])
				if not candles:
					continue

				change_pct = self._calculate_window_change(candles)
				levels = calculate_support_resistance(candles, lookback=20)
				bars = [
					{
						"o": float(row[1]),
						"h": float(row[2]),
						"l": float(row[3]),
						"c": float(row[4]),
					}
					for row in candles[-self._recent_bar_count(tf) :]
				]
				timeframes[tf] = MarketTimeframe(
					timeframe=tf,
					bars=bars,
					change_pct=change_pct,
					support=levels.get("support"),
					resistance=levels.get("resistance"),
				)

			snapshots[symbol] = MarketSnapshot(
				symbol=symbol,
				last_price=float(last_prices.get(symbol, 0.0) or 0.0),
				change_1m_pct=change_1m_pct,
				timeframes=timeframes,
			)

		return MarketEnvelope(retrieved_at=retrieved_at, snapshots=snapshots)

	def build_account_state(self, symbols: List[str]) -> AccountState:
		self.settings = get_settings()
		balances: Dict[str, float] = {}
		available_balances: Dict[str, float] = {}

		try:
			client = get_trading_client()
			response = client.get_account_balance()
			print(f"[DataCollector] get_account_balance response code: {response.get('code')}, data exists: {bool(response.get('data'))}")
			if response.get("code") == "0" and response.get("data"):
				for account in response["data"]:
					for detail in account.get("details", []):
						ccy = detail.get("ccy")
						avail = float(detail.get("availBal", 0) or 0.0)
						cash = float(detail.get("cashBal", 0) or 0.0)
						equity = float(detail.get("eq", 0) or 0.0)
						total = max(avail, cash, equity)
						print(f"[DataCollector] {ccy}: avail={avail}, cash={cash}, equity={equity}, total={total}")
						if total <= 0 and avail <= 0:
							print(f"[DataCollector] Skipping {ccy} (total={total}, avail={avail})")
							continue
						balances[ccy] = total
						available_balances[ccy] = avail
						print(f"[DataCollector] Added {ccy}: {total}")
		except Exception as exc:  # pragma: no cover - network failures
			print(f"[DataCollector] Failed to fetch account balances: {exc}")

		try:
			last_prices = get_last_prices(symbols)
		except Exception as exc:  # pragma: no cover - network failures
			print(f"[DataCollector] Failed to refresh prices for positions: {exc}")
			last_prices = {}

		positions: Dict[str, AccountPosition] = {}
		for ccy, amount in balances.items():
			if ccy == "USDT" or amount <= 0:
				continue
			symbol = f"{ccy}-USDT"
			price = float(last_prices.get(symbol, 0.0) or 0.0)
			if price <= 0:
				continue
			positions[symbol] = AccountPosition(
				symbol=symbol,
				side="long",
				amount=amount,
				value_usdt=amount * price,
			)

		recent_trades = self._collect_recent_trades(limit=10)
		recent_decisions = self._collect_recent_decisions(limit=5)

		return AccountState(
			balances=balances,
			available_balances=available_balances,
			positions=positions,
			recent_trades=recent_trades,
			recent_decisions=recent_decisions,
		)

	@staticmethod
	def _calculate_recent_change(candles: List[List[Any]]) -> float:
		if len(candles) < 2:
			return 0.0
		try:
			prev = float(candles[-2][4])
			last = float(candles[-1][4])
			return ((last - prev) / prev) if prev else 0.0
		except (TypeError, ValueError):
			return 0.0

	@staticmethod
	def _calculate_window_change(candles: List[List[Any]]) -> float:
		if len(candles) < 2:
			return 0.0
		try:
			first = float(candles[0][4])
			last = float(candles[-1][4])
			return ((last - first) / first) if first else 0.0
		except (TypeError, ValueError):
			return 0.0

	@staticmethod
	def _recent_bar_count(timeframe: str) -> int:
		return {
			"1m": 20,
			"5m": 24,
			"15m": 16,
			"1H": 12,
			"4H": 12,
			"1D": 7,
		}.get(timeframe, 10)

	@staticmethod
	def _collect_recent_trades(limit: int) -> List[Dict[str, Any]]:
		payload: List[Dict[str, Any]] = []
		# 1) Load locally recorded trades (from our own executions)
		try:
			trades = get_trades()
			for trade in trades[:limit]:
				payload.append(
					{
						"id": trade.id,
						"timestamp": trade.timestamp.isoformat() if trade.timestamp else None,
						"symbol": trade.symbol,
						"side": trade.side,
						"amount": trade.base_amount,
						"price_usdt": trade.price_usdt,
						"notional_usdt": trade.notional_usdt,
						"status": trade.status,
						"source": "db",
					}
				)
		except Exception as exc:  # pragma: no cover - database failures
			print(f"[DataCollector] Failed to load trades from DB: {exc}")

		# 2) Try to augment with recent fills from OKX directly (so agent sees external/manual fills)
		try:
			client = get_trading_client()
			fills_resp = client.get_recent_fills(inst_type="SPOT", limit=max(10, limit))
			fills = fills_resp.get("data", []) if isinstance(fills_resp, dict) else []
			for f in fills[:limit]:
				# OKX fields may vary; commonly: instId, side, fillPx, fillSz, ordId, ts
				inst_id = f.get("instId") or f.get("instId".lower())
				side = (f.get("side") or "").lower()
				price = float(f.get("fillPx") or f.get("px") or 0.0)
				size = float(f.get("fillSz") or f.get("sz") or 0.0)
				ord_id = f.get("ordId") or f.get("billId") or f.get("orderId") or None
				ts = f.get("ts") or f.get("fillTime")
				try:
					# ts is in milliseconds per OKX; normalize to ISO
					iso_ts = None
					if ts is not None:
						millis = int(ts)
						from datetime import datetime, timezone
						iso_ts = datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc).isoformat()
				except Exception:
					iso_ts = None

				payload.append(
					{
						"id": ord_id or f.get("tradeId") or None,
						"timestamp": iso_ts,
						"symbol": inst_id,
						"side": side,
						"amount": size,
						"price_usdt": price,
						"notional_usdt": (price * size) if price and size else None,
						"status": "executed",
						"source": "okx",
					}
				)
		except Exception as exc:  # pragma: no cover - network/permission failures
			print(f"[DataCollector] Warning: failed to fetch OKX fills: {exc}")

		# 3) Sort by timestamp desc when possible and trim to limit
		def _ts_key(x: Dict[str, Any]):
			val = x.get("timestamp")
			try:
				from datetime import datetime
				return datetime.fromisoformat(val.replace("Z", "+00:00")) if isinstance(val, str) else datetime.min
			except Exception:
				return 0
		payload.sort(key=_ts_key, reverse=True)
		return payload[:limit]

	@staticmethod
	def _collect_recent_decisions(limit: int) -> List[Dict[str, Any]]:
		try:
			decisions = get_recent_decisions(limit=limit)
		except Exception as exc:  # pragma: no cover - database failures
			print(f"[DataCollector] Failed to load decisions: {exc}")
			return []

		payload: List[Dict[str, Any]] = []
		for record in decisions:
			payload.append(
				{
					"id": record.id,
					"timestamp": record.timestamp.isoformat() if record.timestamp else None,
					"role": record.role,
					"provider": record.provider,
					"confidence": record.confidence,
					"rationale": record.rationale,
					"execution_results": record.execution_results,
				}
			)
		return payload

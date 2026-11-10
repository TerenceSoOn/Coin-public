from __future__ import annotations
import threading
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from .config import get_settings
from .okx.client import get_recent_candles
from .decision.engine import run_decision_once
from .db.repository import get_latest_strategist_insight, get_active_plan


def _ensure_aware(dt: datetime | None) -> datetime | None:
    """Ensure a datetime is timezone-aware (UTC if naive)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()
_last_prices: dict[str, float] = {}


def _fetch_job():
    """Fetch market data every 1 minute and check for price spikes"""
    s = get_settings()
    
    if s.paused:
        print(f"[Scheduler] ⏸️  System PAUSED - skipping fetch job")
        return
    
    print(f"\n[Scheduler] Running 1m fetch job at {datetime.now(timezone.utc)}")
    print(f"[Scheduler] Monitoring symbols: {', '.join(s.symbols)}")
    
    # Fetch latest candle for each symbol to compute 1m change
    for sym in s.symbols:
        try:
            bars = get_recent_candles(sym, limit=2)
            if len(bars) >= 2:
                prev = float(bars[-2][4])
                now = float(bars[-1][4])
                if prev > 0:
                    change = (now - prev) / prev * 100.0
                    print(f"[Scheduler] {sym}: {now:.2f} ({change:+.3f}%)")
                    
                    if abs(change) >= s.price_spike_trigger_pct:
                        print(f"[Scheduler] 🚨 SPIKE DETECTED for {sym}: {change:+.3f}% (trigger: {s.price_spike_trigger_pct}%)")
                        print(f"[Scheduler] Triggering immediate AI decision...")
                        run_decision_once(symbols=[sym], force_strategist=True)
                        break  # Only trigger once per cycle
        except Exception as e:
            print(f"[Scheduler] Error fetching {sym}: {e}")


def _pipeline_job():
    """Run orchestrated pipeline when strategist/tactician/executor work is due."""
    s = get_settings()
    
    if s.paused:
        print(f"[Scheduler] ⏸️  System PAUSED - skipping pipeline job")
        return
    
    now = datetime.now(timezone.utc)
    due_symbols: list[tuple[str, bool]] = []  # (symbol, force_strategist)

    for symbol in s.symbols:
        force_strategist = False

        insight = get_latest_strategist_insight(symbol)
        if insight is None:
            force_strategist = True
        else:
            expires_at = _ensure_aware(insight.expires_at)
            
            if expires_at is None or expires_at <= now:
                force_strategist = True

        plan = get_active_plan(symbol)

        should_run = False
        if force_strategist:
            should_run = True
        elif plan is None:
            # No active plan; tactician should craft a new one
            should_run = True
        else:
            status = (plan.status or "").lower()
            if status in {"ready", "in_progress"}:
                should_run = True
            elif status == "hold":
                # Ensure next_eval_at is timezone-aware for comparison
                next_eval = _ensure_aware(plan.next_eval_at)
                
                if next_eval is None or next_eval <= now:
                    should_run = True

        if should_run:
            due_symbols.append((symbol, force_strategist))

    if not due_symbols:
        print(f"[Scheduler] No symbols due at {now.isoformat()} (pipeline idle)")
        return

    print(
        f"\n[Scheduler] Pipeline scan @ {now.isoformat()} for symbols: "
        + ", ".join(sym for sym, _ in due_symbols)
    )

    for symbol, force in due_symbols:
        try:
            run_decision_once(symbols=[symbol], force_strategist=force)
        except Exception as exc:  # pragma: no cover - defensive logging
            print(f"[Scheduler] Error running pipeline for {symbol}: {exc}")


def start_scheduler():
    global _scheduler
    if _scheduler:
        print("[Scheduler] Already running")
        return
    
    s = get_settings()
    print(f"\n[Scheduler] Starting background scheduler")
    print(f"[Scheduler] - Fetch job: every 1 minute")
    print(f"[Scheduler] - Pipeline job: every {s.ai_cadence_minutes} minutes")
    print(f"[Scheduler] - Spike trigger: {s.price_spike_trigger_pct}%")
    
    _scheduler = BackgroundScheduler(timezone=str(timezone.utc))
    _scheduler.add_job(_fetch_job, "interval", minutes=1, id="fetch")
    _scheduler.add_job(_pipeline_job, "interval", minutes=get_settings().ai_cadence_minutes, id="ai")
    _scheduler.start()
    
    print("[Scheduler] ✓ Scheduler started successfully\n")


def update_ai_cadence():
    """Reschedule the pipeline job to use the latest scan cadence."""
    global _scheduler
    if not _scheduler:
        print("[Scheduler] update_ai_cadence(): Scheduler not started; nothing to update")
        return
    try:
        s = get_settings()
        minutes = max(1, int(s.ai_cadence_minutes))
        job = _scheduler.get_job("ai")
        if job is None:
            _scheduler.add_job(_pipeline_job, "interval", minutes=minutes, id="ai")
            print(f"[Scheduler] Pipeline job added: every {minutes} minutes")
        else:
            _scheduler.reschedule_job("ai", trigger="interval", minutes=minutes)
            print(f"[Scheduler] Pipeline job rescheduled: every {minutes} minutes")
    except Exception as e:
        print(f"[Scheduler] Failed to update AI cadence: {e}")

# Architecture (Spot-Only OKX, Personal Project)

## Overview
- Backend: FastAPI (Python) + Pydantic + SQLAlchemy + Alembic
- Scheduler: APScheduler for periodic and triggered runs
- Database: Postgres (recommended)
- Cache/Queue: Optional Redis (see below)
- UI: Server-rendered (Jinja2 + HTMX + Tailwind) for a simple dashboard/config
- AI: DeepSeek provider first, with a provider interface to add OpenAI/Gemini later
- News/Search: Deferred for MVP; later will be platform-side fetch + summarization (not done by AI tools), cached to reduce costs
- Packaging: Docker Compose for cross-platform runtime (macOS/Linux/Windows)

## Modes (AI is not informed of the mode)
- Paper: simulate fills and PnL locally (no exchange orders)
- Sandbox: use OKX simulated trading (e.g., send `x-simulated-trading: 1` header). No real funds used
- Live: real orders on OKX

The AI receives only market/news/position context and risk constraints. It never sees (and cannot infer) whether the system is in paper/sandbox/live.

## Market scope
- Spot-only trading. Optional spot margin up to 3× leverage cap (configurable, off by default)
- Quote currency: USDT
- Symbols: start with BTC/USDT and ETH/USDT

## Cadence & triggers
- Fetch market data every 1 minute (candles and best bid/ask; order book depth optional)
- Call AI every 10 minutes by default
- Trigger: if the 1-minute price move ≥ 3% for a symbol, run an immediate AI decision for that symbol
- The AI may return `next_decision_in_minutes` to adapt scheduling; guardrails still apply

## Safety guardrails (enforced regardless of AI suggestions)
- Max leverage: 3× (only relevant if spot margin is enabled)
- Max position size per asset: configurable percent of equity (e.g., 10%)
- Max total exposure: configurable (e.g., 20–30% of equity)
- Daily loss cap: configurable (e.g., 2–3% of equity) → cooldown period
- Default SL/TP bands if AI omits them
- Duplicate order protection and idempotency keys

## AI decision flow (high level)
1) **Strategist** collects multi-timeframe market context + account snapshot and emits a cached insight per symbol (4h horizon). Provider/model configurable via env/runtime config.
2) **Tactician** consumes the strategist insight and current state to draft a normalized trade plan (steps, sizing, rationale). Plans are persisted with step metadata.
3) **Executor** evaluates pending plan steps against live market/account data, optionally adjusting sizing before delegating to the legacy OKX executor. Deviations and orders are logged for memory.
4) Guardrails (sizing, leverage, exposure caps) stay enforced before every order; tactician/executor prompts remind AI of constraints.
5) All role inputs/outputs plus execution results are persisted for audit/analytics.

The scheduler still triggers cadence + spike checks, but orchestration is now handled by a thread-pooled pipeline coordinator (per symbol) to keep stages isolated.

## News/Search (Deferred)
- Deferred for MVP to focus on OKX connectivity and AI decision I/O
- Later: platform-side fetch via search/news APIs and RSS; summarize and cache digests

## Optional Redis — do we need it?
- Not strictly required for a personal single-node setup
- Recommended if you need:
  - Distributed or concurrent workers
  - Reliable locks for triggers and idempotency across processes
  - Rate-limit tokens and transient state not ideal for Postgres
- Minimal mode: run without Redis using APScheduler + Postgres and in-process locks

## Data model (current sketch)
- configs, feature_flags
- accounts, balances, positions
- candles (1m), ohlcv cache
- decisions (inputs, outputs, provider, rationale, constraints applied, role metadata)
- strategist_insights (per symbol cached outlook)
- tactician_plans + plan_steps (normalized trade blueprint)
- plan_execution_logs (executor memory + deviations)
- orders, trades (linked to decisions/plan steps)
- news_articles (raw), news_summaries (digest per window and symbol/global)
- runs (scheduler runs), audit_log (structured, secrets redacted)

## Cross-platform notes
- Docker/Compose ensures parity across macOS, Linux, and Windows (use WSL2 on Windows)
- No OS-specific code planned; paths and line endings handled by Docker

## Configuration
- `.env` for secrets (OKX API key/secret/passphrase; DeepSeek API key)
- Runtime-modifiable config via API (GET/PUT /api/config); not hard-coded
- App config includes: mode (paper/sandbox/live), symbols, cadences, trigger thresholds (e.g., 3%/1m), margin toggle, max leverage cap (3×), risk caps, provider selection, budgets

## Next steps
- Confirm defaults (news provider, budgets, caps)
- Generate scaffold with FastAPI app, scheduler, DB migrations, minimal UI
- Add OKX client (spot + optional margin), DeepSeek provider, and decision loop stub

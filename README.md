# AI-Assisted Crypto Trading (OKX, Spot-Only)

Personal project to trade BTC/USDT and ETH/USDT on OKX using a FastAPI backend and a small web UI. The system fetches market data frequently, asks an AI model (DeepSeek first) for a decision, enforces safety guardrails, and executes spot trades accordingly. News/search is deferred for MVP. Docker-based and cross-platform.

## Key choices
- Market: Spot only (with optional spot margin up to 3× cap)
- Quote currency: USDT
- Symbols: BTC/USDT, ETH/USDT (extensible)
- Cadence: fetch every 1m; AI every 10m unless a 1m price move ≥ 3% triggers an immediate AI call
- AI provider: DeepSeek first; plug-in interface for others (OpenAI, Gemini)
- News/search: deferred for MVP (will be platform-side later)
- Modes: paper, sandbox (simulated trading header), live; AI is not told the mode
- Safety: strict caps (max leverage 3× if margin enabled, position sizing, daily loss cap, cooldowns)

## Cross-platform
- Uses Docker and Docker Compose; should run on macOS, Linux, and Windows (WSL2 recommended on Windows)

## Getting started
1. Copy `.env.example` to `.env` and set API keys (OKX, DeepSeek)
2. Run locally (no Docker) for quick testing:

```bash
# macOS/Linux (zsh)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# ensure Python can find the package under backend/
export PYTHONPATH=backend

# start the server with auto-reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Or build and run with Docker Compose:

```bash
docker compose up --build
```

3. Open http://localhost:8000 for a simple status page
4. API endpoints:
	- GET /health
	- GET /api/config
	- PUT /api/config
	- POST /api/decision/run (manual trigger)

### Running only the database in Docker (app locally)

```bash
# start Postgres only (detached)
docker compose up -d db

# stop it later
docker compose stop db
```

## Docs
- `docs/ARCHITECTURE.md` — System design, modes, triggers, safety, optional Redis
- `docs/AI_CONTRACT.md` — The exact inputs sent to the AI and expected outputs

> NOTE: This repo is not yet scaffolded. Once you confirm defaults in the discussion, we’ll generate the FastAPI app, scheduler, database, and minimal UI.
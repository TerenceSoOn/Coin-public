from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List
import os
from dotenv import load_dotenv

# In MVP, settings are stored in-memory with env defaults; later backed by DB

# Load .env at import time for local dev (Docker uses env_file already)
load_dotenv()


def _resolve_model_default(provider_env: str, model_env: str) -> str | None:
    provider = os.environ.get(provider_env, os.environ.get("AI_PROVIDER", "deepseek"))
    provider = (provider or "deepseek").lower()
    if provider == "gemini":
        fallback = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    else:
        fallback = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    return os.environ.get(model_env, fallback)

class Settings(BaseModel):
    # Modes
    mode: str = Field(default=os.environ.get("APP_MODE", "sandbox"))  # sandbox|live (paper deprecated)

    # Control
    paused: bool = False  # Runtime pause control

    # Symbols and cadence
    symbols: List[str] = Field(default_factory=lambda: ["BTC-USDT", "ETH-USDT"])
    ai_cadence_minutes: int = int(os.environ.get("AI_CADENCE_MINUTES", 5))
    strategist_cadence_minutes: int = int(os.environ.get("STRATEGIST_CADENCE_MINUTES", 240))
    price_spike_trigger_pct: float = float(os.environ.get("PRICE_SPIKE_TRIGGER_PCT", 3.0))

    # Margin (spot only; margin optional)
    margin_enabled: bool = os.environ.get("MARGIN_ENABLED", "false").lower() == "true"
    max_leverage: int = int(os.environ.get("MAX_LEVERAGE", 3))

    # Risk caps in direct USD amounts (not percentages)
    max_position_usd: float = float(os.environ.get("MAX_POSITION_USD", 100.0))
    max_total_exposure_usd: float = float(os.environ.get("MAX_TOTAL_EXPOSURE_USD", 200.0))
    daily_loss_cap_usd: float = float(os.environ.get("DAILY_LOSS_CAP_USD", 50.0))

    # Providers
    ai_provider: str = os.environ.get("AI_PROVIDER", "deepseek")
    deepseek_model: str = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    # Use a v1beta-supported model by default
    gemini_model: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    # Role-specific providers/models
    strategist_provider: str = os.environ.get("STRATEGIST_PROVIDER", os.environ.get("AI_PROVIDER", "deepseek"))
    strategist_model: str | None = _resolve_model_default("STRATEGIST_PROVIDER", "STRATEGIST_MODEL")
    tactician_provider: str = os.environ.get("TACTICIAN_PROVIDER", os.environ.get("AI_PROVIDER", "deepseek"))
    tactician_model: str | None = _resolve_model_default("TACTICIAN_PROVIDER", "TACTICIAN_MODEL")
    executor_provider: str = os.environ.get("EXECUTOR_PROVIDER", os.environ.get("AI_PROVIDER", "deepseek"))
    executor_model: str | None = _resolve_model_default("EXECUTOR_PROVIDER", "EXECUTOR_MODEL")

    # Role cadence controls
    strategist_cache_ttl_minutes: int = int(
        os.environ.get(
            "STRATEGIST_CACHE_TTL_MINUTES",
            os.environ.get("STRATEGIST_CADENCE_MINUTES", 240),
        )
    )
    tactician_hold_retry_minutes: int = int(os.environ.get("TACTICIAN_HOLD_RETRY_MINUTES", 30))
    executor_hold_retry_minutes: int = int(os.environ.get("EXECUTOR_HOLD_RETRY_MINUTES", 5))
    executor_deviation_threshold_pct: float = float(os.environ.get("EXECUTOR_DEVIATION_THRESHOLD_PCT", 1.5))
    plan_execution_timeout_minutes: int = int(os.environ.get("PLAN_EXECUTION_TIMEOUT_MINUTES", 15))
    max_symbol_workers: int = int(os.environ.get("MAX_SYMBOL_WORKERS", 4))

    # Tactician auto-upgrade policy
    tactician_auto_upgrade_enabled: bool = os.environ.get("TACTICIAN_AUTO_UPGRADE_ENABLED", "true").lower() == "true"
    tactician_auto_confidence_threshold: float = float(os.environ.get("TACTICIAN_AUTO_CONFIDENCE_THRESHOLD", 0.80))
    tactician_auto_min_order_usd: float = float(os.environ.get("TACTICIAN_AUTO_MIN_ORDER_USD", 10.0))

    # OKX
    okx_base_url: str = os.environ.get("OKX_BASE_URL", "https://www.okx.com")
    okx_api_key: str | None = os.environ.get("OKX_API_KEY")
    okx_api_secret: str | None = os.environ.get("OKX_API_SECRET")
    okx_api_passphrase: str | None = os.environ.get("OKX_API_PASSPHRASE")
    okx_use_sandbox: bool = os.environ.get("OKX_USE_SANDBOX", "false").lower() == "true"

    # AI keys
    deepseek_api_key: str | None = os.environ.get("DEEPSEEK_API_KEY")
    gemini_api_key: str | None = os.environ.get("GEMINI_API_KEY")

_settings = Settings()

def get_settings() -> Settings:
    return _settings

# Simple in-memory update; later we can persist to DB

def update_settings(**kwargs) -> Settings:
    global _settings
    data = _settings.model_dump()
    for k, v in kwargs.items():
        if k not in data:
            raise ValueError(f"Unknown config field: {k}")
        data[k] = v
    _settings = Settings(**data)
    return _settings

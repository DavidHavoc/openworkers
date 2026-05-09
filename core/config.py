"""Centralised application settings via pydantic-settings.

All environment variables the project reads live here. Every module that
needs a config value calls ``get_settings()`` rather than touching
``os.environ`` directly. This gives us:

* Type safety — every field has a Python type, so ``settings.dry_run`` is
  always ``bool``, not the string ``"true"`` or ``"false"``.
* A single source of truth — the full list of recognised env vars is in one
  place, with defaults documented inline.
* Test isolation — ``reset_settings()`` clears the ``lru_cache`` singleton,
  so ``monkeypatch.setenv`` in a test takes effect for the next call.

Configuration
-------------
Settings are read from environment variables first, with ``.env`` as a
fallback (standard pydantic-settings behaviour). Unknown env vars are
silently ignored (``extra="ignore"``).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── infrastructure ──────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = ""
    database_url: str = ""
    session_ttl_seconds: int = 30 * 24 * 3600
    session_backend: str = ""

    # ── search cache ────────────────────────────────────────────────────
    search_cache_enabled: bool = True
    search_cache_ttl_seconds: int = 86400

    # ── LLM routing ─────────────────────────────────────────────────────
    dry_run: bool = True
    thesis_quality_provider: str = "anthropic"
    thesis_quality_model: str = "claude-sonnet-4-20250514"
    thesis_balanced_provider: str = "openai"
    thesis_balanced_model: str = "gpt-4o-mini"
    thesis_cheap_provider: str = "deepseek"
    thesis_cheap_model: str = "deepseek-chat"

    # ── API keys ────────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    deepseek_api_key: str = ""

    # ── budget guard ────────────────────────────────────────────────────
    max_budget_usd: Optional[float] = None
    budget_output_token_floor: int = 500

    # ── resilience ──────────────────────────────────────────────────────
    resilience_retry_attempts: int = 3
    resilience_retry_base_sec: float = 0.5
    resilience_retry_max_sec: float = 8.0
    resilience_breaker_fail_max: int = 5
    resilience_breaker_reset_sec: int = 60

    # ── observability ───────────────────────────────────────────────────
    environment: str = "development"
    log_level: str = "info"

    # ── embedding cache ─────────────────────────────────────────────────
    embedding_cache_dir: str = ""

    @field_validator("max_budget_usd", mode="before")
    @classmethod
    def _coerce_budget(cls, v: Any) -> Optional[float]:
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None


_settings_env_file: Optional[str] = ".env"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings singleton.

    Cached on first call. Use ``reset_settings()`` in tests after
    ``monkeypatch.setenv`` so the next call re-reads the patched vars.
    """
    return Settings(_env_file=_settings_env_file)  # type: ignore[call-arg]


def reset_settings(*, use_env_file: bool = True) -> None:
    """Clear the cached singleton.

    In tests, call ``reset_settings(use_env_file=False)`` so Settings reads
    only real environment variables (not the dev ``.env``), making
    ``monkeypatch.setenv`` the sole source of test config.
    """
    global _settings_env_file
    _settings_env_file = ".env" if use_env_file else None
    get_settings.cache_clear()

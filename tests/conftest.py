"""Shared pytest fixtures.

``_reset_singletons`` clears process-wide cached singletons before and after
every test. Without this, ``monkeypatch.setenv`` / ``monkeypatch.delenv``
calls would be invisible to cached objects (Settings, SearchCache,
ProviderBreakerRegistry) that were already constructed from earlier env state.
"""

from __future__ import annotations

import pytest

from core.config import reset_settings
from core.embedding_cache import reset_embedding_cache
from providers.resilience import reset_default_registry
from tools.cache import reset_default_cache


@pytest.fixture(autouse=True)
def _reset_singletons() -> None:
    # use_env_file=False: tests must not be affected by the dev .env file;
    # monkeypatch.setenv() is the sole source of test configuration.
    reset_settings(use_env_file=False)
    reset_default_cache()
    reset_default_registry()
    reset_embedding_cache()
    yield  # type: ignore[misc]
    reset_settings(use_env_file=False)
    reset_default_cache()
    reset_default_registry()
    reset_embedding_cache()

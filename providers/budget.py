"""Per-session hard budget ceiling.

When the resilience layer makes sessions reliable enough to actually finish,
runaway cost becomes the next failure mode: a recursive citation-chase, a
mis-priced provider switch, or a prompt blow-up can chew through dollars
faster than a human notices.

This module wires a hard ceiling into ``UnifiedLLM.generate()``. Before each
provider call, we estimate the cost of the call and refuse to run it if the
running session spend + estimate would exceed ``MAX_BUDGET_USD``. The
fallback chain still works: a provider whose estimate doesn't fit gets
skipped, and a cheaper one downstream may fit.

Scope is per-session, not per-process — multiple concurrent
``ThesisOrchestrator.execute()`` calls on a shared ``UnifiedLLM`` (e.g. via
the FastAPI surface) must not share a counter. We use ``contextvars`` so
each session's guard is local to its asyncio task tree without threading
the guard through every agent's signature.

Configuration
-------------
``MAX_BUDGET_USD``           — per-session ceiling. Unset/empty → guard is off.
``BUDGET_OUTPUT_TOKEN_FLOOR`` — minimum assumed output tokens (default 500).
"""

from __future__ import annotations

import contextvars
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Same approximation the rest of the codebase uses (chars/3.5 ≈ tokens).
# Avoids pulling tiktoken just for a soft estimate.
_CHARS_PER_TOKEN = 3.5

# Cost rates per 1K tokens — kept in sync with providers/unified.py. Worth
# duplicating here so the budget guard has no dependency on UnifiedLLM
# internals; if the rates ever diverge, that's a code smell to flag.
COST_PER_1K_TOKENS: dict[str, float] = {
    "anthropic": 0.015,
    "openai": 0.005,
    "deepseek": 0.0014,
}

_DEFAULT_RATE = 0.005


class BudgetExceededError(RuntimeError):
    """Raised when a single provider's estimate would push spend past the cap.

    The ``generate()`` loop catches this and treats it like any other
    provider failure — it tries the next provider in the chain, and only
    bubbles up to the caller if every provider's estimate is over budget.
    """


def _env_float(name: str) -> Optional[float]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("BUDGET: ignoring non-numeric %s=%r", name, raw)
        return None


def _output_floor() -> int:
    raw = os.environ.get("BUDGET_OUTPUT_TOKEN_FLOOR", "").strip()
    if not raw:
        return 500
    try:
        n = int(raw)
        return max(0, n)
    except ValueError:
        return 500


class BudgetGuard:
    """Tracks running spend and refuses calls that would exceed a per-session cap.

    Guard semantics:
    * ``max_usd`` of ``None`` (the default when the env var is unset) means
      the guard is *off* — every call is allowed and only ``record_actual``
      bookkeeping happens.
    * ``estimate(prompt, system_prompt, provider)`` returns the upper-bound
      USD cost we expect this single call to incur. The estimate assumes
      ``len(input_chars + output_floor_chars) / 3.5`` total tokens at the
      provider's per-1K rate. Conservative on purpose: we'd rather skip
      a borderline call than blow past the cap.
    * ``check(estimate)`` returns False if running through with this
      estimate would exceed the cap. Does not mutate state.
    * ``record_actual(actual_cost)`` adds the real cost (post-call) to
      the running tally.

    The guard is also a context manager. ``with BudgetGuard():`` installs
    itself as the current guard via ``contextvars`` so async-spawned
    agents pick it up implicitly.
    """

    def __init__(
        self,
        max_usd: Optional[float] = None,
        output_token_floor: Optional[int] = None,
    ) -> None:
        env_cap = _env_float("MAX_BUDGET_USD")
        self.max_usd = max_usd if max_usd is not None else env_cap
        self.output_token_floor = (
            output_token_floor if output_token_floor is not None else _output_floor()
        )
        self.spent_usd = 0.0
        self._token: Optional[contextvars.Token[Optional["BudgetGuard"]]] = None

    @property
    def enabled(self) -> bool:
        return self.max_usd is not None

    def remaining(self) -> Optional[float]:
        if self.max_usd is None:
            return None
        return max(0.0, self.max_usd - self.spent_usd)

    def estimate(self, prompt: str, system_prompt: str, provider: str) -> float:
        """Conservative upper-bound USD cost for one ``generate()`` call.

        Sums input chars + an output-token floor (default 500), converts to
        tokens via the same chars/3.5 heuristic the rest of the codebase
        uses, and multiplies by the provider's per-1K rate. Outputs are
        usually shorter than inputs for our agents, but we err high so the
        cap stays honest under occasional verbose responses.
        """
        input_chars = len(prompt) + len(system_prompt)
        input_tokens = input_chars / _CHARS_PER_TOKEN
        total_tokens = input_tokens + self.output_token_floor
        rate = COST_PER_1K_TOKENS.get(provider, _DEFAULT_RATE)
        return (total_tokens / 1000) * rate

    def check(self, estimate_usd: float) -> bool:
        """True if a call costing ``estimate_usd`` fits inside the remaining cap.

        When the guard is off (``max_usd`` is None), always returns True —
        the cap is the no-op. When on, returns True iff
        ``spent + estimate <= max_usd``.
        """
        if self.max_usd is None:
            return True
        return self.spent_usd + estimate_usd <= self.max_usd

    def reserve(self, estimate_usd: float) -> None:
        """Like ``check`` but raises ``BudgetExceededError`` when over.

        Convenience for callers that want to fail-fast rather than branch.
        Does not mutate ``spent_usd`` — the caller will record the actual
        cost via ``record_actual`` after the call succeeds.
        """
        if not self.check(estimate_usd):
            raise BudgetExceededError(
                f"Estimated ${estimate_usd:.6f} would exceed remaining budget "
                f"${self.remaining():.6f} (cap ${self.max_usd:.6f}, "
                f"already spent ${self.spent_usd:.6f})"
            )

    def record_actual(self, actual_cost_usd: float) -> None:
        """Add a completed call's real cost to the running tally.

        Called even when the guard is off so ``spent_usd`` remains useful
        for observability/logging downstream.
        """
        if actual_cost_usd > 0:
            self.spent_usd += actual_cost_usd

    def reset(self) -> None:
        """Zero the running spend. Used between sessions reusing one guard."""
        self.spent_usd = 0.0

    # ── contextvars plumbing ────────────────────────────────────────────

    def __enter__(self) -> "BudgetGuard":
        self._token = _current_guard.set(self)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._token is not None:
            _current_guard.reset(self._token)
            self._token = None


_current_guard: contextvars.ContextVar[Optional[BudgetGuard]] = contextvars.ContextVar(
    "openworkers_budget_guard", default=None
)


def get_current_guard() -> Optional[BudgetGuard]:
    """Return the BudgetGuard installed by the nearest enclosing ``with`` block, if any."""
    return _current_guard.get()

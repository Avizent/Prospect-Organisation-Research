"""Token-to-USD pricing for Anthropic models.

Used by :class:`backend.cost_control.job_budget.JobBudgetGuard` and
:class:`backend.cost_control.budget_state.BudgetState` to estimate and
record costs.

Pricing-table contract
----------------------
* Each entry maps a model id (string) to two USD-per-million-token rates:
  ``input`` and ``output``.
* The model id is matched exactly. There is **no** prefix matching,
  no version fall-through, and no "latest" alias resolution — those
  abstractions would hide a price change inside an alias.
* An unknown model id raises :class:`UnknownModel` (a subclass of
  :class:`RunawayTrapFired`) so the cost-control layer fails closed.
* Estimates are pessimistic: they price the *requested* ``max_tokens``
  as if the model returned that many output tokens, regardless of how
  much it actually returns. Real cost (post-call) uses real token
  counts.

Reviewer note (Opus 4.7 audit)
------------------------------
If you change any number in :data:`PRICES`, also bump
:data:`KNOWN_PRICES_AS_OF`. Stale pricing is one of the most
dangerous silent failures in a cost-control system.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.cost_control.exceptions import UnknownModel


# ---------------------------------------------------------------------------
# Source of truth
# ---------------------------------------------------------------------------

KNOWN_PRICES_AS_OF: str = "2026-05-27"
"""ISO date the prices below were last verified."""

PRICING_SOURCE: str = "Anthropic official pricing page"
"""Human-readable provenance for the values in :data:`PRICES`."""


@dataclass(frozen=True)
class ModelPrice:
    """Per-million-token pricing for a single model."""

    input_usd_per_mtok: float
    output_usd_per_mtok: float


PRICES: dict[str, ModelPrice] = {
    "claude-sonnet-4-6": ModelPrice(
        input_usd_per_mtok=3.00,
        output_usd_per_mtok=15.00,
    ),
    "claude-haiku-4-5": ModelPrice(
        input_usd_per_mtok=1.00,
        output_usd_per_mtok=5.00,
    ),
    "claude-opus-4-7": ModelPrice(
        input_usd_per_mtok=5.00,
        output_usd_per_mtok=25.00,
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_price(model: str) -> ModelPrice:
    """Return the :class:`ModelPrice` for *model* or raise :class:`UnknownModel`.

    Treated as a trap firing because the call cannot be cost-controlled
    against a model we cannot price.
    """
    try:
        return PRICES[model]
    except KeyError:
        raise UnknownModel(
            f"no pricing entry for model {model!r}",
            context={"model": model, "known_models": sorted(PRICES.keys())},
        ) from None


def estimate_call_cost_usd(
    model: str,
    input_tokens: int,
    max_output_tokens: int,
) -> float:
    """Return a pessimistic pre-call cost estimate, in USD.

    The estimate prices the *requested* ``max_output_tokens`` as if the
    model returned that many output tokens. That is the upper bound for
    the call, and it is what the per-job budget guard checks against.

    Raises :class:`UnknownModel` if the model is not in :data:`PRICES`.
    Raises :class:`ValueError` if either token count is negative.
    """
    if input_tokens < 0:
        raise ValueError("input_tokens must be >= 0")
    if max_output_tokens < 0:
        raise ValueError("max_output_tokens must be >= 0")

    price = get_price(model)
    return (
        input_tokens * price.input_usd_per_mtok / 1_000_000
        + max_output_tokens * price.output_usd_per_mtok / 1_000_000
    )


def actual_call_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Return the realised cost of a completed call, in USD.

    Used after the SDK returns to record actual spend against the
    job and the daily/monthly state files.
    """
    if input_tokens < 0:
        raise ValueError("input_tokens must be >= 0")
    if output_tokens < 0:
        raise ValueError("output_tokens must be >= 0")

    price = get_price(model)
    return (
        input_tokens * price.input_usd_per_mtok / 1_000_000
        + output_tokens * price.output_usd_per_mtok / 1_000_000
    )

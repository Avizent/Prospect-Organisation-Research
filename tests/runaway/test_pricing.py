"""Pricing table — values, math, and unknown-model trap."""

from __future__ import annotations

import pytest

from backend.cost_control.exceptions import UnknownModel
from backend.cost_control.pricing import (
    KNOWN_PRICES_AS_OF,
    PRICES,
    PRICING_SOURCE,
    actual_call_cost_usd,
    estimate_call_cost_usd,
    get_price,
)


# ---------------------------------------------------------------------------
# Source-of-truth metadata
# ---------------------------------------------------------------------------

def test_known_prices_as_of_is_set() -> None:
    assert KNOWN_PRICES_AS_OF == "2026-05-27"


def test_pricing_source_is_set() -> None:
    assert PRICING_SOURCE == "Anthropic official pricing page"


def test_table_contains_expected_models() -> None:
    assert set(PRICES.keys()) == {
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
        "claude-opus-4-7",
    }


# ---------------------------------------------------------------------------
# Per-model values — these are part of the operational contract.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("model", "expected_input", "expected_output"),
    [
        ("claude-sonnet-4-6", 3.00, 15.00),
        ("claude-haiku-4-5", 1.00, 5.00),
        ("claude-opus-4-7", 5.00, 25.00),
    ],
)
def test_rates_match_specified_values(
    model: str, expected_input: float, expected_output: float
) -> None:
    price = get_price(model)
    assert price.input_usd_per_mtok == expected_input
    assert price.output_usd_per_mtok == expected_output


# ---------------------------------------------------------------------------
# Estimate math is pessimistic (uses max_output_tokens, not actual output)
# ---------------------------------------------------------------------------

def test_estimate_uses_max_output_tokens() -> None:
    # 1000 input tokens * $3/Mtok = $0.003
    # 2000 output tokens * $15/Mtok = $0.030
    # Total = $0.033
    assert estimate_call_cost_usd("claude-sonnet-4-6", 1000, 2000) == pytest.approx(0.033)


def test_estimate_zero_tokens_is_zero() -> None:
    assert estimate_call_cost_usd("claude-haiku-4-5", 0, 0) == 0.0


def test_estimate_rejects_negative_inputs() -> None:
    with pytest.raises(ValueError):
        estimate_call_cost_usd("claude-sonnet-4-6", -1, 100)
    with pytest.raises(ValueError):
        estimate_call_cost_usd("claude-sonnet-4-6", 100, -1)


def test_estimate_unknown_model_raises_unknown_model() -> None:
    with pytest.raises(UnknownModel):
        estimate_call_cost_usd("claude-fictional-9", 1000, 2000)


# ---------------------------------------------------------------------------
# Actual cost math
# ---------------------------------------------------------------------------

def test_actual_cost_matches_realised_usage() -> None:
    # 5000 input tokens * $5/Mtok = $0.025
    # 1000 output tokens * $25/Mtok = $0.025
    # Total = $0.050
    assert actual_call_cost_usd("claude-opus-4-7", 5000, 1000) == pytest.approx(0.050)


def test_actual_cost_rejects_negative_inputs() -> None:
    with pytest.raises(ValueError):
        actual_call_cost_usd("claude-haiku-4-5", -1, 100)
    with pytest.raises(ValueError):
        actual_call_cost_usd("claude-haiku-4-5", 100, -1)


def test_actual_cost_unknown_model_raises_unknown_model() -> None:
    with pytest.raises(UnknownModel):
        actual_call_cost_usd("claude-fictional-9", 100, 100)


# ---------------------------------------------------------------------------
# Pessimism — estimate >= actual whenever output_tokens <= max_output_tokens
# ---------------------------------------------------------------------------

def test_estimate_is_upper_bound() -> None:
    est = estimate_call_cost_usd("claude-sonnet-4-6", 4000, 8000)
    act = actual_call_cost_usd("claude-sonnet-4-6", 4000, 5000)
    assert est >= act

"""Claude API list prices and the cost of a turn. Used by the daily spend cap and by the eval."""

from __future__ import annotations

# USD per million tokens (Anthropic first-party list prices; 5-minute cache writes = 1.25x input).
PRICES = {
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.10},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20},
}


def cost_usd(usage: dict[str, int], model: str) -> float:
    """Cost of the tokens in `usage`. An unknown model is priced like the dearest one (errs high)."""
    p = PRICES.get(model, PRICES["claude-opus-5-5"])
    return (usage.get("input_tokens", 0) * p["input"] + usage.get("output_tokens", 0) * p["output"]
            + usage.get("cache_creation_input_tokens", 0) * p["cache_write"]
            + usage.get("cache_read_input_tokens", 0) * p["cache_read"]) / 1e6

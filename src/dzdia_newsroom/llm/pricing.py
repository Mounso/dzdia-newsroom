"""Estimated cost of a call, for logs only (billing is the source of truth).

Standard paid tier, USD per million tokens, read on https://ai.google.dev/gemini-api/docs/pricing
on 2026-11-13. Thinking tokens are billed as output. Long-context tiers (prompts over 200k
tokens) are not modeled: this project's prompts stay far below.
"""

from dataclasses import dataclass

from dzdia_newsroom.llm.base import Usage


@dataclass(frozen=True)
class Price:
    input_per_m: float
    output_per_m: float


PRICES: dict[str, Price] = {
    "gemini-3.1-flash-lite-preview": Price(input_per_m=0.25, output_per_m=1.50),
    "gemini-3.1-pro-preview": Price(input_per_m=2.00, output_per_m=12.00),
}


def estimate_cost_usd(model: str, usage: Usage) -> float | None:
    """None when the model has no known price or the token counts are missing."""
    price = PRICES.get(model)
    if price is None or usage.input_tokens is None or usage.output_tokens is None:
        return None
    output = usage.output_tokens + (usage.thinking_tokens or 0)
    return (usage.input_tokens * price.input_per_m + output * price.output_per_m) / 1_000_000

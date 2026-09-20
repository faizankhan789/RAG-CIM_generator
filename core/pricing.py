"""Shared Claude API pricing table — one source of truth so cost estimates
never drift between core/db_log.py's cim_generation_log rows and
core/template_store.py's template-audit cost tracking.

Rates are USD per million tokens, current as of this file's last edit —
update here only, both callers pick it up automatically.
"""

from __future__ import annotations

PRICE_PER_M_INPUT: dict[str, float] = {"haiku": 0.80, "sonnet": 3.00, "opus": 15.00}
PRICE_PER_M_OUTPUT: dict[str, float] = {"haiku": 4.00, "sonnet": 15.00, "opus": 75.00}


def estimate_price(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for one Claude API call. Unrecognized model names
    (tier not found in the tables above) fall back to a conservative
    sonnet-ish rate ($1.00 in / $5.00 out per M) rather than guessing $0."""
    m = model.lower()
    tier = next((k for k in PRICE_PER_M_INPUT if k in m), None)
    in_rate = PRICE_PER_M_INPUT.get(tier, 1.00)
    out_rate = PRICE_PER_M_OUTPUT.get(tier, 5.00)
    return round(input_tokens * in_rate / 1_000_000 + output_tokens * out_rate / 1_000_000, 8)

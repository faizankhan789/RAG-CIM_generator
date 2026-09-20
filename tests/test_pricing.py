"""Tests for core.pricing — the shared Claude API pricing table used by both
core/db_log.py (cim_generation_log) and core/template_store.py
(template_audit_log) so cost estimates never drift between the two.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.pricing import estimate_price


def test_haiku_rate():
    # $0.80/M in, $4.00/M out
    price = estimate_price("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
    assert price == 0.80 + 4.00


def test_sonnet_rate():
    price = estimate_price("claude-sonnet-4-6", 1_000_000, 1_000_000)
    assert price == 3.00 + 15.00


def test_opus_rate():
    price = estimate_price("claude-opus-4-1", 1_000_000, 1_000_000)
    assert price == 15.00 + 75.00


def test_unrecognized_model_falls_back_to_conservative_rate():
    price = estimate_price("some-future-model-xyz", 1_000_000, 1_000_000)
    assert price == 1.00 + 5.00


def test_zero_tokens_is_zero_cost():
    assert estimate_price("claude-haiku-4-5-20251001", 0, 0) == 0.0


def test_matches_manual_calculation_for_small_realistic_call():
    # A tiny audit call: ~2000 input tokens, ~400 output tokens, haiku tier
    price = estimate_price("claude-haiku-4-5-20251001", 2000, 400)
    expected = round(2000 * 0.80 / 1_000_000 + 400 * 4.00 / 1_000_000, 8)
    assert price == expected


def test_case_insensitive_model_matching():
    assert estimate_price("HAIKU-4-5", 1000, 1000) == estimate_price("haiku-4-5", 1000, 1000)

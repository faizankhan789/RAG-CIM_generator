"""Tests for aggregator merge logic and formatter output shaping."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from nodes.aggregator import _merge_dicts, _merge_value


class TestMergeValue:
    def test_scalar_first_wins(self):
        assert _merge_value("Acme", "Other") == "Acme"

    def test_null_filled_by_incoming(self):
        assert _merge_value(None, 5_000_000) == 5_000_000

    def test_null_incoming_ignored(self):
        assert _merge_value("Acme", None) == "Acme"

    def test_lists_extended_no_duplicates(self):
        result = _merge_value(["Market risk"], ["Market risk", "Regulatory risk"])
        assert result == ["Market risk", "Regulatory risk"]

    def test_dicts_recursed(self):
        base = {"a": "x", "b": None}
        incoming = {"b": "y", "c": "z"}
        result = _merge_value(base, incoming)
        assert result["a"] == "x"
        assert result["b"] == "y"
        assert result["c"] == "z"


class TestMergeDicts:
    def test_new_key_added(self):
        result = _merge_dicts({"a": "x"}, {"b": "y"})
        assert result["b"] == "y"

    def test_nested_sections_merged(self):
        base = {
            "executive_summary": {"business_overview": "Acme is a logistics company", "investment_highlights": ["Strong growth"]},
        }
        incoming = {
            "executive_summary": {"business_overview": None, "investment_highlights": ["Profitable"], "asking_price_and_terms": "$50M"},
        }
        result = _merge_dicts(base, incoming)
        es = result["executive_summary"]
        assert es["business_overview"] == "Acme is a logistics company"
        assert "Profitable" in es["investment_highlights"]
        assert es["asking_price_and_terms"] == "$50M"

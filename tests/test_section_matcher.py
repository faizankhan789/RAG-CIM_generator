"""Tests for core.section_matcher — deterministic heading-to-CIM-taxonomy matching."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.section_matcher import CANONICAL_SECTIONS, match_sections


def test_exact_canonical_text_matches():
    result = match_sections(["I. Executive Summary"])
    assert result["I. Executive Summary"] == "I. Executive Summary"


def test_synonym_matches_and_keeps_original_label():
    result = match_sections(["Financials"])
    assert result["III. Financial Information"] == "Financials"


def test_numbered_and_punctuated_prefixes_stripped():
    result = match_sections(["3. Financial Overview"])
    assert result["III. Financial Information"] == "3. Financial Overview"


def test_case_insensitive():
    result = match_sections(["EXECUTIVE SUMMARY"])
    assert result["I. Executive Summary"] == "EXECUTIVE SUMMARY"


def test_unmatched_heading_is_dropped_not_guessed():
    result = match_sections(["Table of Contents", "Our Awesome Team Photos"])
    assert result == {}


def test_first_match_wins_on_duplicate_canonical_target():
    result = match_sections(["Executive Summary", "Summary"])
    assert result["I. Executive Summary"] == "Executive Summary"


def test_all_ten_canonical_sections_present():
    assert len(CANONICAL_SECTIONS) == 10
    assert CANONICAL_SECTIONS[0] == "I. Executive Summary"
    assert CANONICAL_SECTIONS[-1] == "X. Appendix"

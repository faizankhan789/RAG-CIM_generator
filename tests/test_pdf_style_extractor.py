"""Tests for core.pdf_style_extractor's pure helper functions (no PDF parsing yet)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.pdf_style_extractor import (
    _detect_alignment,
    _detect_bullet_glyph,
    _font_stack_for,
    _hex_color,
)


def test_hex_color_from_packed_int():
    assert _hex_color(0x0000FF) == "#0000ff"
    assert _hex_color(0x000000) == "#000000"


def test_font_stack_matches_known_family():
    assert "Helvetica" in _font_stack_for("Helvetica-Bold", is_serif_flag=False)
    assert "Georgia" in _font_stack_for("TimesNewRomanPSMT", is_serif_flag=True)


def test_font_stack_falls_back_on_serif_flag():
    stack = _font_stack_for("SomeCustomFontXYZ", is_serif_flag=True)
    assert "Georgia" in stack or "serif" in stack


def test_font_stack_falls_back_to_sans_by_default():
    stack = _font_stack_for("SomeCustomFontXYZ", is_serif_flag=False)
    assert "Helvetica" in stack or "sans-serif" in stack


def test_bullet_glyph_picks_most_common():
    assert _detect_bullet_glyph(["• one", "• two", "- three"]) == "•"


def test_bullet_glyph_defaults_when_none_found():
    assert _detect_bullet_glyph(["no bullets here", "or here"]) == "•"


def test_alignment_centered():
    # page width 600, span centered around x=250-350 -> center 300 == page_width/2
    assert _detect_alignment((250, 0, 350, 20), page_width=600) == "center"


def test_alignment_left():
    # span sits near the left edge, far from page center
    assert _detect_alignment((20, 0, 120, 20), page_width=600) == "left"


import pytest

from core.pdf_style_extractor import NoExtractableTextError, extract_style_profile
from tests.pdf_helpers import make_no_text_pdf, make_text_pdf, make_text_pdf_with_band


def test_extract_raises_on_no_text_layer():
    with pytest.raises(NoExtractableTextError):
        extract_style_profile(make_no_text_pdf())


def test_extract_basic_pdf_shape_and_values():
    template, warnings = extract_style_profile(make_text_pdf())
    assert template["id"] == "custom-upload"
    assert template["palette"]["primary"] == "#0000ff"   # heading color
    assert template["palette"]["mid"] == "#000000"        # body color
    assert "Helvetica" in template["fonts"]["heading"]
    assert template["headings"]["I. Executive Summary"] == "I. Executive Summary"
    # Only one heading was in the PDF -> the other 9 canonical sections are unmatched
    assert len(warnings) == 9
    assert "I. Executive Summary" not in warnings


def test_extract_detects_band_color():
    template, _warnings = extract_style_profile(make_text_pdf_with_band((1, 0.6, 0)))
    assert template["palette"]["accent"] == "#ff9900"
    assert "#ff9900" in template["section_header_override"]

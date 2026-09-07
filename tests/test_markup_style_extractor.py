"""Tests for core.markup_style_extractor — the HTML/XML counterpart to
tests/test_pdf_style_extractor.py."""

from __future__ import annotations

import pytest

from core.markup_style_extractor import extract_style_profile
from core.pdf_style_extractor import NoExtractableTextError
from tests.template_helpers import make_custom_schema_xml, make_empty_html, make_text_html


def test_extract_raises_on_empty_html():
    with pytest.raises(NoExtractableTextError):
        extract_style_profile(make_empty_html())


def test_extract_raises_on_empty_bytes():
    with pytest.raises(NoExtractableTextError):
        extract_style_profile(b"")


def test_extract_basic_html_shape_and_values():
    template, warnings = extract_style_profile(make_text_html())
    assert template["id"] == "custom-upload"
    assert template["palette"]["primary"] == "#8b0000"    # h1 color
    assert template["palette"]["mid"] == "#333333"        # p color
    assert template["palette"]["accent"] == "#f4e4c1"     # .band background-color
    assert "Georgia" in template["fonts"]["heading"]
    assert "Helvetica" in template["fonts"]["body"] or "Arial" in template["fonts"]["body"]
    assert template["headings"]["I. Executive Summary"] == "Executive Summary"
    assert len(warnings) == 9


def test_heading_and_body_fonts_dont_bleed_into_each_other():
    """Regression check: heading and body CSS rules must resolve independently —
    caught during manual verification where an empty body-side lookup fell back
    to the heading's font instead of the body's own <style> rule."""
    template, _warnings = extract_style_profile(make_text_html())
    assert template["fonts"]["heading"] != template["fonts"]["body"]


def test_custom_xml_schema_falls_back_to_defaults_without_crashing():
    """Real text in unrecognized tags (no h1/p/etc) — no design hints found,
    but must degrade to defaults rather than raising on a non-empty file."""
    template, warnings = extract_style_profile(make_custom_schema_xml())
    assert template["palette"]["primary"] == "#000000"
    assert len(warnings) == 10  # no headings matched at all

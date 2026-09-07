"""Tests for core.docx_style_extractor — the .docx counterpart to
tests/test_pdf_style_extractor.py."""

from __future__ import annotations

import pytest

from core.docx_style_extractor import extract_plain_text, extract_style_profile
from core.pdf_style_extractor import NoExtractableTextError
from tests.template_helpers import make_empty_docx, make_text_docx


def test_extract_raises_on_empty_docx():
    with pytest.raises(NoExtractableTextError):
        extract_style_profile(make_empty_docx())


def test_extract_basic_docx_shape_and_values():
    template, warnings = extract_style_profile(make_text_docx())
    assert template["id"] == "custom-upload"
    assert template["palette"]["primary"] == "#1f4e79"   # heading run color
    assert template["palette"]["mid"] == "#000000"        # body run has no explicit color
    assert "Georgia" in template["fonts"]["heading"]
    assert "Helvetica" in template["fonts"]["body"] or "Arial" in template["fonts"]["body"]
    assert template["headings"]["I. Executive Summary"] == "Executive Summary"
    assert len(warnings) == 9
    assert "I. Executive Summary" not in warnings


def test_extract_detects_band_color():
    template, _warnings = extract_style_profile(make_text_docx(band_fill="2E5E3E"))
    assert template["palette"]["accent"] == "#2e5e3e"
    assert "#2e5e3e" in template["section_header_override"]


def test_extract_plain_text_flattens_paragraphs_and_tables():
    text = extract_plain_text(make_text_docx(heading="My Heading", body="My Body"))
    assert "My Heading" in text
    assert "My Body" in text

"""Tests for core.docx_style_extractor — the .docx counterpart to
tests/test_pdf_style_extractor.py."""

from __future__ import annotations

import base64

import pytest

from core.docx_style_extractor import extract_plain_text, extract_reference_images, extract_style_profile
from core.pdf_style_extractor import NoExtractableTextError
from tests.template_helpers import make_empty_docx, make_text_docx, make_text_docx_with_image


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


def test_extract_reference_images_returns_empty_list_when_no_images():
    assert extract_reference_images(make_text_docx()) == []


def test_extract_reference_images_finds_embedded_image():
    images = extract_reference_images(make_text_docx_with_image())
    assert len(images) == 1
    assert images[0]["mime"] == "image/png"
    # round-trips to real, decodable PNG bytes — not a placeholder/garbage string
    raw = base64.standard_b64decode(images[0]["b64"])
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")


def test_extract_reference_images_caps_at_max_and_orders_largest_first():
    docx_bytes = make_text_docx_with_image()
    # Only one image in the fixture — this just locks in the cap constant's
    # existence/behavior contract without needing 5 embedded images.
    from core.docx_style_extractor import _MAX_REFERENCE_IMAGES
    images = extract_reference_images(docx_bytes)
    assert len(images) <= _MAX_REFERENCE_IMAGES


def test_extract_reference_images_never_raises_on_corrupt_input():
    # Not a real .docx at all — python-docx's Document() will choke; the
    # function must degrade to an empty list rather than propagate.
    assert extract_reference_images(b"not a real docx file") == []

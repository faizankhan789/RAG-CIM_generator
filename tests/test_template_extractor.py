"""Tests for core.template_extractor — the format dispatcher in front of
core/pdf_style_extractor.py, core/docx_style_extractor.py, and
core/markup_style_extractor.py."""

from __future__ import annotations

import pytest

from core.pdf_style_extractor import NoExtractableTextError
from core.template_extractor import UnsupportedTemplateFileError, extract_style_profile
from tests.pdf_helpers import make_text_pdf
from tests.template_helpers import make_custom_schema_xml, make_text_docx, make_text_html


def test_dispatches_pdf():
    template, _warnings = extract_style_profile(make_text_pdf(), "template.pdf")
    assert template["id"] == "custom-upload"


def test_dispatches_docx():
    template, _warnings = extract_style_profile(make_text_docx(), "template.docx")
    assert template["id"] == "custom-upload"


def test_dispatches_html():
    template, _warnings = extract_style_profile(make_text_html(), "template.html")
    assert template["id"] == "custom-upload"


def test_dispatches_xml():
    template, _warnings = extract_style_profile(make_custom_schema_xml(), "template.xml")
    assert template["id"] == "custom-upload"


def test_rejects_unsupported_extension():
    with pytest.raises(UnsupportedTemplateFileError):
        extract_style_profile(b"whatever", "notes.txt")


def test_rejects_unopenable_doc_file():
    """A .doc that python-docx can't open (legacy binary, corrupt, whatever) must
    raise the clear dispatcher-level error, not python-docx's raw exception."""
    with pytest.raises(UnsupportedTemplateFileError):
        extract_style_profile(b"this is not a real ole/zip package", "legacy.doc")


def test_empty_pdf_still_raises_no_extractable_text():
    """Format-specific 'no content' errors must still surface through the
    dispatcher unchanged — not get relabeled as UnsupportedTemplateFileError."""
    from tests.pdf_helpers import make_no_text_pdf
    with pytest.raises(NoExtractableTextError):
        extract_style_profile(make_no_text_pdf(), "scanned.pdf")

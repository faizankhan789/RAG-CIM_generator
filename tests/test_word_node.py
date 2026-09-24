"""Tests for nodes.word._to_text — specifically the legacy .doc conversion
path added via core.office_convert. Mocks convert_legacy entirely; no real
soffice invocation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv

load_dotenv()  # nodes.word imports core.llm, which reads CIM_MODEL at import time

from nodes.word import _to_text
from tests.template_helpers import make_text_docx


def test_docx_extension_reads_directly_no_conversion_attempted(tmp_path: Path):
    p = tmp_path / "template.docx"
    p.write_bytes(make_text_docx(heading="Heading", body="Body text here"))
    with patch("core.office_convert.convert_legacy") as mock_convert:
        text = _to_text(p)
    mock_convert.assert_not_called()
    assert "Heading" in text
    assert "Body text here" in text


def test_legacy_doc_is_converted_then_read(tmp_path: Path):
    converted = make_text_docx(heading="Converted Heading", body="Converted body")
    p = tmp_path / "legacy.doc"
    p.write_bytes(b"pretend legacy ole bytes")
    with patch("core.office_convert.convert_legacy", return_value=(converted, "docx")) as mock_convert:
        text = _to_text(p)
    mock_convert.assert_called_once_with(b"pretend legacy ole bytes", "doc")
    assert "Converted Heading" in text
    assert "Converted body" in text


def test_legacy_doc_falls_back_to_raw_text_when_conversion_fails(tmp_path: Path):
    """Regression guard: a conversion failure (soffice missing, corrupt
    file, whatever) must not raise out of _to_text — it must degrade to the
    same best-effort raw-text fallback that existed before this feature,
    never a hard failure for the whole word_node batch."""
    from core.office_convert import LegacyConversionError

    p = tmp_path / "legacy.doc"
    p.write_bytes(b"some raw legacy bytes with readable fragments")
    with patch("core.office_convert.convert_legacy", side_effect=LegacyConversionError("soffice missing")):
        text = _to_text(p)
    assert "some raw legacy bytes" in text  # the old best-effort raw-text fallback

"""Tests for nodes.ppt — the legacy .ppt conversion path added via
core.office_convert, plus the item.content temp-file suffix fix (was
hardcoded ".pptx" regardless of the real uploaded extension, so a legacy
.ppt sent as inline content never reached the conversion branch at all).
Mocks convert_legacy entirely; no real soffice invocation.
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv

load_dotenv()  # nodes.ppt imports core.llm, which reads CIM_MODEL at import time

import pytest

from models import FileItem
from nodes.ppt import _to_text, _process_one
from tests.template_helpers import make_text_pptx


def test_pptx_extension_reads_directly_no_conversion_attempted(tmp_path: Path):
    p = tmp_path / "template.pptx"
    p.write_bytes(make_text_pptx(heading="Heading", body="Body text here"))
    with patch("core.office_convert.convert_legacy") as mock_convert:
        text = _to_text(p)
    mock_convert.assert_not_called()
    assert "Heading" in text
    assert "Body text here" in text


def test_legacy_ppt_is_converted_then_read(tmp_path: Path):
    converted = make_text_pptx(heading="Converted Heading", body="Converted body")
    p = tmp_path / "legacy.ppt"
    p.write_bytes(b"pretend legacy ole bytes")
    with patch("core.office_convert.convert_legacy", return_value=(converted, "pptx")) as mock_convert:
        text = _to_text(p)
    mock_convert.assert_called_once_with(b"pretend legacy ole bytes", "ppt")
    assert "Converted Heading" in text
    assert "Converted body" in text


def test_legacy_ppt_conversion_failure_propagates():
    """Unlike word.py's .doc path, ppt.py never had a raw-text fallback —
    a conversion failure must still surface as a clear error (via the
    existing _process_one exception handling), not silently succeed with
    garbage or an empty result."""
    from core.office_convert import LegacyConversionError
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".ppt", delete=False) as tmp:
        tmp.write(b"pretend legacy ole bytes")
        tmp_path = Path(tmp.name)
    try:
        with patch("core.office_convert.convert_legacy", side_effect=LegacyConversionError("soffice missing")):
            with pytest.raises(LegacyConversionError):
                _to_text(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_item_content_uses_real_extension_not_hardcoded_pptx():
    """Regression guard: item.content's temp file used to always get a
    ".pptx" suffix regardless of item.label's real extension, so a legacy
    .ppt sent as inline content would never reach the .ppt conversion
    branch in _to_text (which keys off path.suffix). A wrong/mismatched
    suffix here means Presentation() is asked to open legacy binary bytes
    as if they were already .pptx, which fails outright."""
    pptx_bytes = make_text_pptx(heading="Inline Heading", body="Inline body")
    item = FileItem(
        label="deck.pptx",
        content=base64.standard_b64encode(pptx_bytes).decode(),
    )
    with patch("core.office_convert.convert_legacy") as mock_convert, \
         patch("nodes.ppt.extract_from_content", return_value="extracted"):
        result = await _process_one(item)
    mock_convert.assert_not_called()  # real .pptx content — no conversion needed
    assert result.error is None


@pytest.mark.asyncio
async def test_legacy_ppt_via_inline_content_now_reaches_conversion():
    """The actual bug-fix confirmation: before the suffix fix, a legacy
    .ppt sent as item.content always got a hardcoded .pptx temp-file
    suffix, so _to_text's `path.suffix.lower() == ".ppt"` check could
    never fire and convert_legacy was never even attempted for this
    upload path — Presentation() would just raise on the legacy bytes."""
    converted = make_text_pptx(heading="Converted", body="Body")
    item = FileItem(
        label="old_deck.ppt",
        content=base64.standard_b64encode(b"pretend legacy ole bytes").decode(),
    )
    with patch("core.office_convert.convert_legacy", return_value=(converted, "pptx")) as mock_convert, \
         patch("nodes.ppt.extract_from_content", return_value="extracted"):
        result = await _process_one(item)
    mock_convert.assert_called_once_with(b"pretend legacy ole bytes", "ppt")
    assert result.error is None

"""Verifies generate_cim_html attaches the uploaded template file correctly
per format — real vision/document block for PDF, inlined text for
docx/html/xml — without making a real Claude API call. Mocks
core.llm.get_client and captures the `messages` kwarg passed to
client.messages.stream(...).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()  # core.llm reads CIM_MODEL at import time, same as server.py does

from core.llm import generate_cim_html
from tests.pdf_helpers import make_text_pdf
from tests.template_helpers import make_text_docx, make_text_html


def _fake_client(captured: dict):
    """A MagicMock standing in for anthropic.AsyncAnthropic — captures the
    `messages` kwarg of the one call generate_cim_html makes, and returns a
    canned final message shaped like a real streamed response."""
    client = MagicMock()

    final_msg = MagicMock()
    final_msg.content = [MagicMock(text="<!DOCTYPE html><html><body>fake cim</body></html>")]
    final_msg.usage = MagicMock(input_tokens=10, output_tokens=10)
    final_msg.stop_reason = "end_turn"

    stream_cm = MagicMock()
    stream_cm.__aenter__ = AsyncMock(return_value=MagicMock(get_final_message=AsyncMock(return_value=final_msg)))
    stream_cm.__aexit__ = AsyncMock(return_value=False)

    def _stream(**kwargs):
        captured["messages"] = kwargs["messages"]
        return stream_cm

    client.messages.stream = _stream
    return client


async def _run(custom_template: dict) -> list[dict]:
    captured: dict = {}
    with patch("core.llm.get_client", return_value=_fake_client(captured)):
        await generate_cim_html(
            all_findings=[],
            listing_xml="<listing/>",
            listing_name="Acme Bakery",
            asking_price="$500,000",
            custom_template=custom_template,
        )
    return captured["messages"][0]["content"]


def _template_with(file_b64: str, file_ext: str) -> dict:
    return {
        "id": "custom-upload",
        "name": "Your Uploaded Template",
        "allow_brand_override": False,
        "palette": {"primary": "#000000", "accent": "#000000", "light": "#ffffff", "mid": "#000000"},
        "fonts": {"heading": "serif", "body": "sans-serif"},
        "layout_notes": "",
        "cover_override": "",
        "section_header_override": "",
        "headings": {},
        "file_b64": file_b64,
        "file_ext": file_ext,
    }


@pytest.mark.asyncio
async def test_pdf_attaches_real_document_block():
    import base64
    pdf_b64 = base64.standard_b64encode(make_text_pdf()).decode()
    content = await _run(_template_with(pdf_b64, "pdf"))

    doc_blocks = [b for b in content if b.get("type") == "document"]
    assert len(doc_blocks) == 1
    assert doc_blocks[0]["source"]["media_type"] == "application/pdf"
    assert doc_blocks[0]["source"]["data"] == pdf_b64

    intro_blocks = [b for b in content if b.get("type") == "text" and "DESIGN REFERENCE ONLY" in b.get("text", "")]
    assert len(intro_blocks) == 1


@pytest.mark.asyncio
async def test_docx_inlines_extracted_text_no_document_block():
    import base64
    docx_bytes = make_text_docx(heading="My Custom Heading", body="My Custom Body")
    docx_b64 = base64.standard_b64encode(docx_bytes).decode()
    content = await _run(_template_with(docx_b64, "docx"))

    assert not [b for b in content if b.get("type") == "document"]
    ref_blocks = [b for b in content if b.get("type") == "text" and "DESIGN REFERENCE ONLY" in b.get("text", "")]
    assert len(ref_blocks) == 1
    assert "My Custom Heading" in ref_blocks[0]["text"]
    assert "My Custom Body" in ref_blocks[0]["text"]


@pytest.mark.asyncio
async def test_html_inlines_raw_markup_no_document_block():
    import base64
    html_bytes = make_text_html(heading="My HTML Heading", body="My HTML Body")
    html_b64 = base64.standard_b64encode(html_bytes).decode()
    content = await _run(_template_with(html_b64, "html"))

    assert not [b for b in content if b.get("type") == "document"]
    ref_blocks = [b for b in content if b.get("type") == "text" and "DESIGN REFERENCE ONLY" in b.get("text", "")]
    assert len(ref_blocks) == 1
    assert "My HTML Heading" in ref_blocks[0]["text"]
    assert "<style>" in ref_blocks[0]["text"]  # real markup, not stripped/summarized


@pytest.mark.asyncio
async def test_no_attachment_block_when_file_b64_missing():
    """Older custom_template dicts (pre-this-feature) have no file_b64 —
    behavior must be unchanged: no design-reference block at all."""
    template = _template_with("", "")
    del template["file_b64"]
    del template["file_ext"]
    content = await _run(template)

    assert not [b for b in content if "DESIGN REFERENCE ONLY" in b.get("text", "")]
    assert not [b for b in content if b.get("type") == "document"]

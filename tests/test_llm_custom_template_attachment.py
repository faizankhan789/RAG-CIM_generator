"""Verifies generate_cim_html attaches the uploaded template file correctly
per format — real vision/document block for PDF, inlined text for
docx/pptx/html/xml — without making a real Claude API call. Mocks
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
from tests.template_helpers import (
    make_text_docx,
    make_text_docx_with_image,
    make_text_html,
    make_text_pptx,
    make_text_pptx_with_image,
)


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
async def test_pptx_inlines_extracted_text_no_document_block():
    import base64
    pptx_bytes = make_text_pptx(heading="My Custom Heading", body="My Custom Body")
    pptx_b64 = base64.standard_b64encode(pptx_bytes).decode()
    content = await _run(_template_with(pptx_b64, "pptx"))

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
async def test_docx_with_embedded_image_attaches_it_as_vision_block():
    """Regression guard for the .docx blind spot: before extract_reference_images,
    a Word template's embedded images (logos, photos, decorative graphics) were
    invisible to Claude — only flattened text made it into the prompt. Now the
    image bytes ride along as a real vision block, same mechanism as any other
    design-reference image."""
    import base64
    docx_bytes = make_text_docx_with_image()
    docx_b64 = base64.standard_b64encode(docx_bytes).decode()
    content = await _run(_template_with(docx_b64, "docx"))

    image_blocks = [b for b in content if b.get("type") == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"]["media_type"] == "image/png"

    caption_blocks = [
        b for b in content
        if b.get("type") == "text" and "embedded in the uploaded Word template" in b.get("text", "")
    ]
    assert len(caption_blocks) == 1


@pytest.mark.asyncio
async def test_docx_without_embedded_image_attaches_no_image_block():
    import base64
    docx_bytes = make_text_docx()  # no image in this fixture
    docx_b64 = base64.standard_b64encode(docx_bytes).decode()
    content = await _run(_template_with(docx_b64, "docx"))

    assert not [b for b in content if b.get("type") == "image"]


@pytest.mark.asyncio
async def test_pptx_with_embedded_image_attaches_it_as_vision_block():
    """Same regression guard as the .docx case above, for .pptx: embedded
    slide images (logos, photos, decorative graphics) must ride along as a
    real vision block, not just get dropped along with the flattened text."""
    import base64
    pptx_bytes = make_text_pptx_with_image()
    pptx_b64 = base64.standard_b64encode(pptx_bytes).decode()
    content = await _run(_template_with(pptx_b64, "pptx"))

    image_blocks = [b for b in content if b.get("type") == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"]["media_type"] == "image/png"

    caption_blocks = [
        b for b in content
        if b.get("type") == "text" and "embedded in the uploaded PowerPoint template" in b.get("text", "")
    ]
    assert len(caption_blocks) == 1


@pytest.mark.asyncio
async def test_pptx_without_embedded_image_attaches_no_image_block():
    import base64
    pptx_bytes = make_text_pptx()  # no image in this fixture
    pptx_b64 = base64.standard_b64encode(pptx_bytes).decode()
    content = await _run(_template_with(pptx_b64, "pptx"))

    assert not [b for b in content if b.get("type") == "image"]


@pytest.mark.asyncio
async def test_custom_template_prompt_has_no_competing_default_design_spec():
    """Static regression guard for the "no deterministic default design" fix:
    the composed prompt for a custom-template job must NOT carry a default
    visual design (cover/TOC/section-header/footer specs) alongside the
    override — that's what caused two real bugs (Kline Paper CIM: gradient
    cover + colored section-header band, neither present in the real file)
    even with an explicit override describing something else. The uploaded
    file (attached separately) plus the MANDATORY TEMPLATE OVERRIDE are now
    the only source of visual design; there must be nothing left in the base
    prompt for them to compete with."""
    import base64
    html_b64 = base64.standard_b64encode(make_text_html()).decode()
    content = await _run(_template_with(html_b64, "html"))

    prompt_blocks = [
        b for b in content
        if b.get("type") == "text" and "MANDATORY TEMPLATE OVERRIDE" in b.get("text", "")
    ]
    assert len(prompt_blocks) == 1
    prompt_text = prompt_blocks[0]["text"]

    # None of the old hardcoded default-design sections may appear anywhere in the prompt.
    for removed_default in (
        "PAGE 1 — COVER", "PAGE 2 — TABLE OF CONTENTS", "KEY METRICS STRIP",
        "CONTENT SECTIONS — LAYOUT", "SECTION HEADER:", "SECTION FOOTER", "LAST PAGE — DISCLAIMER",
        "Full-width band: gradient",
    ):
        assert removed_default not in prompt_text

    # Content/data-accuracy rules must remain untouched — removing the default design
    # spec is scoped to visual design only, never to relaxing the anti-fabrication rules
    # or the 10-section content backbone.
    assert "FINANCIAL NUMBER RULES" in prompt_text
    assert "CRITICAL DATA RULES" in prompt_text
    assert "CIM STRUCTURE — 10 SECTIONS" in prompt_text


@pytest.mark.asyncio
async def test_builtin_template_path_never_gets_the_custom_template_prompt():
    """The custom-template prompt (design attached separately, no default
    spec) is specific to the uploaded-file job — the 5 built-in templates
    (marker path, chrome-rendered) must never see it."""
    captured: dict = {}
    with patch("core.llm.get_client", return_value=_fake_client(captured)):
        await generate_cim_html(
            all_findings=[],
            listing_xml="<listing/>",
            listing_name="Acme Bakery",
            asking_price="$500,000",
            template_id="classic",
            custom_template=None,
        )
    content = captured["messages"][0]["content"]
    assert not [b for b in content if "MANDATORY TEMPLATE OVERRIDE" in b.get("text", "")]


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


async def _prompt_text() -> str:
    import base64
    content = await _run(_template_with(base64.standard_b64encode(make_text_html()).decode(), "html"))
    return next(b["text"] for b in content if "MANDATORY TEMPLATE OVERRIDE" in b.get("text", ""))


@pytest.mark.asyncio
async def test_template_components_take_priority_over_generic_library():
    """The generic component library (stat strips, card grids…) pulled output
    toward one generic look; the template's own components must come first."""
    prompt = await _prompt_text()
    assert "TEMPLATE FIRST" in prompt
    assert "Never use identical layout for two adjacent sections" not in prompt


@pytest.mark.asyncio
async def test_icons_only_when_the_template_uses_icons():
    prompt = await _prompt_text()
    assert "ONLY if the uploaded template itself uses icons" in prompt


@pytest.mark.asyncio
async def test_prompt_asks_for_design_plan_and_self_check():
    prompt = await _prompt_text()
    assert "DESIGN PLAN" in prompt
    assert "SELF-CHECK" in prompt


@pytest.mark.asyncio
async def test_design_plan_before_doctype_is_stripped_from_output():
    import base64
    captured: dict = {}
    client = _fake_client(captured)
    reply = ("DESIGN PLAN: cover copies template page 1 (photo, framed title).\n\n"
             "<!DOCTYPE html><html><body>real cim</body></html>")
    final_msg = MagicMock(content=[MagicMock(text=reply)],
                          usage=MagicMock(input_tokens=1, output_tokens=1), stop_reason="end_turn")
    stream_cm = MagicMock()
    stream_cm.__aenter__ = AsyncMock(return_value=MagicMock(get_final_message=AsyncMock(return_value=final_msg)))
    stream_cm.__aexit__ = AsyncMock(return_value=False)
    client.messages.stream = lambda **kw: stream_cm
    with patch("core.llm.get_client", return_value=client):
        html = await generate_cim_html(
            all_findings=[], listing_xml="<listing/>", listing_name="Acme", asking_price="",
            custom_template=_template_with(base64.standard_b64encode(make_text_html()).decode(), "html"),
        )
    assert html.startswith("<!DOCTYPE html>")
    assert "DESIGN PLAN" not in html


@pytest.mark.asyncio
async def test_prompt_forbids_inventing_contact_details_the_template_shows():
    """Real run: the Eden template shows a 'Website Link' on its cover and the
    model invented www.<business>.com for a listing that had no website."""
    prompt = await _prompt_text()
    assert "never invent a website" in prompt.lower()


@pytest.mark.asyncio
async def test_self_check_covers_overflowing_kpi_numbers():
    """Real run: large figures overflowed narrow KPI boxes and overlapped."""
    prompt = await _prompt_text()
    self_check = prompt.split("SELF-CHECK", 1)[1]
    assert "overlap" in self_check.lower()


class TestStripInventedContacts:
    """Real run: even with an explicit prompt rule, the model kept inventing
    www.<business>.com because the template's cover shows a website link.
    Enforced in code: a website/email that isn't in the source data is removed."""

    def _strip(self, html, source="Anchor & Vine Hospitality Group, Gulf Coast"):
        from core.llm import _strip_invented_contacts
        return _strip_invented_contacts(html, source)

    def test_invented_website_link_is_removed(self):
        html = ('<html><body><div class="cover"><h1>Anchor</h1>'
                '<a href="https://www.anchorandvinehospitality.com">www.anchorandvinehospitality.com</a>'
                '</div></body></html>')
        out = self._strip(html)
        assert "anchorandvinehospitality" not in out
        assert "<h1>Anchor</h1>" in out

    def test_invented_bare_url_and_email_text_removed_with_their_label(self):
        html = "<p>Website Link: www.fake-site.com</p><p>Contact: info@fake-site.com</p><p>Keep me</p>"
        out = self._strip(html)
        assert "fake-site" not in out
        assert "Website Link" not in out
        assert "<p>Keep me</p>" in out

    def test_real_website_from_source_data_is_kept(self):
        html = '<p>Website: <a href="https://www.realhotel.com">www.realhotel.com</a></p>'
        out = self._strip(html, source="<website>https://www.realhotel.com/</website>")
        assert "www.realhotel.com" in out

    def test_attribute_urls_like_svg_namespace_are_untouched(self):
        html = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"></svg>'
        assert self._strip(html) == html


@pytest.mark.asyncio
async def test_generate_cim_html_strips_invented_website():
    import base64
    client = _fake_client({})
    reply = ('<!DOCTYPE html><html><body><p>Anchor</p>'
             '<p>www.inventedsite.com</p></body></html>')
    final_msg = MagicMock(content=[MagicMock(text=reply)],
                          usage=MagicMock(input_tokens=1, output_tokens=1), stop_reason="end_turn")
    stream_cm = MagicMock()
    stream_cm.__aenter__ = AsyncMock(return_value=MagicMock(get_final_message=AsyncMock(return_value=final_msg)))
    stream_cm.__aexit__ = AsyncMock(return_value=False)
    client.messages.stream = lambda **kw: stream_cm
    with patch("core.llm.get_client", return_value=client):
        html = await generate_cim_html(
            all_findings=["Anchor runs hotels."], listing_xml="<listing/>", listing_name="Anchor", asking_price="",
            custom_template=_template_with(base64.standard_b64encode(make_text_html()).decode(), "html"),
        )
    assert "inventedsite" not in html


def test_strip_invented_contacts_leaves_style_blocks_alone():
    from core.llm import _strip_invented_contacts
    html = ("<html><head><style>.c{background:url(https://example.com/x.png)}</style></head>"
            "<body><p>www.fake.com</p><p>Real</p></body></html>")
    out = _strip_invented_contacts(html, "")
    assert "url(https://example.com/x.png)" in out
    assert "www.fake.com" not in out
    assert "<p>Real</p>" in out


def test_strip_invented_contacts_removes_a_label_element_left_next_to_the_url():
    # Real Eden output: label in its own element, URL as the sibling text node.
    from core.llm import _strip_invented_contacts
    html = ('<div class="closing"><div class="item">'
            '<div class="closing-contact-label">Website</div>\n    www.madeup.com\n</div>'
            '<div class="item"><div class="closing-contact-label">Asking Price</div>$8,500,000</div></div>')
    out = _strip_invented_contacts(html, "$8,500,000")
    assert "Website" not in out
    assert "madeup" not in out
    assert "Asking Price" in out and "$8,500,000" in out


@pytest.mark.asyncio
async def test_kpi_fit_rule_never_trades_exact_figures_for_space():
    """Real run: told to make KPI figures fit, the model abbreviated
    $14,200,000 to $14.2M — the fit rule must forbid that explicitly."""
    self_check = (await _prompt_text()).split("SELF-CHECK", 1)[1]
    assert "NEVER by abbreviating" in self_check


def test_real_url_at_end_of_sentence_is_kept():
    from core.llm import _strip_invented_contacts
    html = "<p>Visit www.realhotel.com.</p>"
    assert _strip_invented_contacts(html, "Website: www.realhotel.com") == html


@pytest.mark.asyncio
async def test_design_plan_is_stripped_even_without_doctype():
    import base64
    client = _fake_client({})
    reply = "DESIGN PLAN: cover copies page 1.\n\n<html><body>real cim</body></html>"
    final_msg = MagicMock(content=[MagicMock(text=reply)],
                          usage=MagicMock(input_tokens=1, output_tokens=1), stop_reason="end_turn")
    stream_cm = MagicMock()
    stream_cm.__aenter__ = AsyncMock(return_value=MagicMock(get_final_message=AsyncMock(return_value=final_msg)))
    stream_cm.__aexit__ = AsyncMock(return_value=False)
    client.messages.stream = lambda **kw: stream_cm
    with patch("core.llm.get_client", return_value=client):
        html = await generate_cim_html(
            all_findings=[], listing_xml="<listing/>", listing_name="Acme", asking_price="",
            custom_template=_template_with(base64.standard_b64encode(make_text_html()).decode(), "html"),
        )
    assert html.startswith("<html>")
    assert "DESIGN PLAN" not in html


def test_invented_url_inside_a_sentence_is_removed():
    # Review finding: edits were discarded when no whole element got emptied.
    from core.llm import _strip_invented_contacts
    out = _strip_invented_contacts("<p>Book online at www.fake-hotel.com today</p>", "Acme Hotels")
    assert "fake-hotel" not in out
    assert "Book online at" in out and "today" in out

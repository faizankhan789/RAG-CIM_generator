"""Verifies the user-uploaded "featured image" (added ahead of CIM generation
via the image-upload popup, see server.py's CIMRequest.featured_image) is:

1. Sent to Claude as a mandatory (non-skippable) vision block with its own
   sequential <!-- IMG:N --> index, continuing after the general image pool.
2. Actually embedded when the LLM places the marker.
3. Force-placed by code (never left out) when the LLM omits the marker —
   for both the custom_template (full HTML doc) and marker (5 built-in
   templates) generation paths — always as normal-flow markup, never
   position:absolute, so it can't overlap anything.
4. Silently dropped (no crash, no phantom instruction) if invalid/corrupt.

No real Claude API call is made — core.llm.get_client is mocked and the
`messages` kwarg / mocked response text are inspected directly.
"""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()  # core.llm reads CIM_MODEL at import time, same as server.py does

from core.llm import generate_cim_html


def _make_png_b64(color=(120, 40, 200), size=(64, 64)) -> str:
    from PIL import Image
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode()


FEATURED = {"b64": _make_png_b64(), "mime": "image/png", "label": "Storefront photo"}


def _fake_client(captured: dict, response_text: str):
    client = MagicMock()

    final_msg = MagicMock()
    final_msg.content = [MagicMock(text=response_text)]
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


async def _run(response_text: str, featured_image, custom_template=None, all_images=None):
    captured: dict = {}
    with patch("core.llm.get_client", return_value=_fake_client(captured, response_text)):
        html = await generate_cim_html(
            all_findings=[],
            listing_xml="<listing/>",
            listing_name="Acme Bakery",
            asking_price="$500,000",
            all_images=all_images or [],
            featured_image=featured_image,
            custom_template=custom_template,
        )
    return html, captured["messages"][0]["content"]


@pytest.mark.asyncio
async def test_featured_image_gets_mandatory_vision_block_at_next_index():
    general_image = {"b64": _make_png_b64(color=(10, 200, 10)), "mime": "image/png", "label": "Gallery shot"}
    _html, content = await _run(
        "<!DOCTYPE html><html><body><!-- IMG:1 --><!-- IMG:2 --></body></html>",
        featured_image=FEATURED,
        custom_template={"id": "custom-upload", "name": "Upload", "file_ext": ""},
        all_images=[general_image],
    )

    mandatory_blocks = [b for b in content if b.get("type") == "text" and "Featured Image — MANDATORY" in b.get("text", "")]
    assert len(mandatory_blocks) == 1
    assert "Image 2" in mandatory_blocks[0]["text"]  # general pool has 1 image -> featured continues at 2
    assert "must NOT be skipped" in mandatory_blocks[0]["text"]
    assert "position:absolute" in mandatory_blocks[0]["text"]  # explicitly banned for this image

    image_blocks = [b for b in content if b.get("type") == "image"]
    assert len(image_blocks) == 2
    assert image_blocks[1]["source"]["data"] == FEATURED["b64"]

    # The general pool keeps its own, separate, still-skippable instruction —
    # the mandatory framing must not leak backwards onto optional images.
    general_blocks = [b for b in content if b.get("type") == "text" and "available)\n" in b.get("text", "") and "Featured" not in b.get("text", "")]
    assert len(general_blocks) == 1
    assert "decide if it adds visual value" in general_blocks[0]["text"]
    assert "SKIP any image that is NOT directly related" in general_blocks[0]["text"]


@pytest.mark.asyncio
async def test_both_prompts_ban_absolute_positioning_for_content_images():
    """Static regression guard: whichever prompt variant is active, the LLM is
    told content images must stay in normal flow. Without this, a future edit
    to either prompt could silently reopen the overlap bug class for images."""
    from core.llm import _HTML_PROMPT, _MARKER_PROMPT

    assert "position:absolute" in _HTML_PROMPT
    assert "content image" in _HTML_PROMPT

    assert "position:absolute" in _MARKER_PROMPT
    assert "SECTION" in _MARKER_PROMPT


@pytest.mark.asyncio
async def test_featured_image_embedded_when_llm_places_marker_custom_path():
    html, _content = await _run(
        "<!DOCTYPE html><html><body><h1>Cover</h1><!-- IMG:1 --></body></html>",
        featured_image=FEATURED,
        custom_template={"id": "custom-upload", "name": "Upload", "file_ext": ""},
    )
    assert "<!-- IMG:1 -->" not in html
    assert f"data:image/png;base64,{FEATURED['b64']}" in html
    assert html.count(FEATURED["b64"]) == 1  # placed once, no duplicate fallback


@pytest.mark.asyncio
async def test_featured_image_fallback_when_marker_omitted_custom_path():
    """LLM writes a full doc but forgets the mandatory marker entirely —
    the image must still end up in the output, as normal-flow markup."""
    html, _content = await _run(
        "<!DOCTYPE html><html><body><h1>Cover with no image marker</h1></body></html>",
        featured_image=FEATURED,
        custom_template={"id": "custom-upload", "name": "Upload", "file_ext": ""},
    )
    assert f"data:image/png;base64,{FEATURED['b64']}" in html
    assert "position:absolute" not in html.split(f"data:image/png;base64,{FEATURED['b64']}")[0][-400:]


@pytest.mark.asyncio
async def test_featured_image_fallback_when_marker_omitted_marker_path():
    """Marker-path (5 built-in templates, custom_template=None): response is
    SECTION-marker text, not a full HTML doc yet. Fallback must land inside
    the first section's normal content flow, right after its opening marker."""
    marker_text = (
        '<!-- INDUSTRY: Food & Beverage -->\n'
        '<!-- SECTION num="I" title="Executive Summary" -->'
        '<p>Some narrative with no image marker at all.</p>'
        '<!-- /SECTION -->'
    )
    html, _content = await _run(marker_text, featured_image=FEATURED, custom_template=None)

    assert f"data:image/png;base64,{FEATURED['b64']}" in html
    section_open = '<!-- SECTION num="I" title="Executive Summary" -->'
    idx_open = html.index(section_open) + len(section_open)
    idx_image = html.index(FEATURED["b64"])
    idx_narrative = html.index("Some narrative")
    # image lands right after the section opens, before the original body content
    assert idx_open <= idx_image < idx_narrative


@pytest.mark.asyncio
async def test_invalid_featured_image_dropped_without_crashing():
    _html, content = await _run(
        "<!DOCTYPE html><html><body>fine</body></html>",
        featured_image={"b64": "not-valid-base64!!", "mime": "image/png", "label": "broken"},
        custom_template={"id": "custom-upload", "name": "Upload", "file_ext": ""},
    )
    assert not [b for b in content if b.get("type") == "text" and "Featured Image — MANDATORY" in b.get("text", "")]


@pytest.mark.asyncio
async def test_no_featured_image_is_a_noop():
    _html, content = await _run(
        "<!DOCTYPE html><html><body>fine</body></html>",
        featured_image=None,
        custom_template={"id": "custom-upload", "name": "Upload", "file_ext": ""},
    )
    assert not [b for b in content if b.get("type") == "image"]
    assert not [b for b in content if "Featured Image — MANDATORY" in b.get("text", "")]

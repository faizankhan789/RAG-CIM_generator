"""Tests for nodes.formatter forwarding the user-uploaded featured_image into
generate_cim_html — across all 3 frontend generation paths (Quick Create,
Pre-defined Custom Template, Upload Your Custom Template). All 3 funnel
through the same view.php chokepoint (selectCimTemplate ->
showCimImageUploadModal -> _beginCimGeneration -> startVerticaCimSse), so the
request shape each one actually POSTs differs only in template_id/
custom_template — this locks down that featured_image survives formatter_node
identically no matter which shape arrives.

Kept separate from tests/test_formatter.py, which currently fails to collect
due to a pre-existing, unrelated bug — not touched here, out of scope.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import AsyncMock, patch

import pytest
from dotenv import load_dotenv

load_dotenv()  # core.llm reads CIM_MODEL at import time, same as server.py does

from nodes.formatter import formatter_node

FEATURED_IMAGE = {"b64": "AAAA", "mime": "image/png", "label": "Storefront photo"}

_BASE_STATE = {
    "all_findings": ["some finding"],
    "all_images": [],
    "listing_xml": "",
    "listing_name": "Acme",
    "asking_price": "$1,000,000",
    "logo_url": "",
}


async def _run(state: dict):
    with patch("nodes.formatter.generate_cim_html", new=AsyncMock(return_value="<html></html>")) as mock_gen:
        await formatter_node(state)
    return mock_gen


@pytest.mark.asyncio
async def test_quick_create_path_forwards_featured_image():
    # Matches selectVerticaQuickCreate's payload shape: template_id="classic", no custom_template.
    state = {**_BASE_STATE, "template_id": "classic", "custom_template": None, "featured_image": FEATURED_IMAGE}
    mock_gen = await _run(state)
    assert mock_gen.call_args.kwargs["featured_image"] == FEATURED_IMAGE
    assert mock_gen.call_args.kwargs["custom_template"] is None


@pytest.mark.asyncio
async def test_predefined_template_path_forwards_featured_image():
    # Matches useCimPreviewTemplate's payload shape: any of the 5 built-in template_ids, no custom_template.
    state = {**_BASE_STATE, "template_id": "minimalist", "custom_template": None, "featured_image": FEATURED_IMAGE}
    mock_gen = await _run(state)
    assert mock_gen.call_args.kwargs["featured_image"] == FEATURED_IMAGE
    assert mock_gen.call_args.kwargs["template_id"] == "minimalist"
    assert mock_gen.call_args.kwargs["custom_template"] is None


@pytest.mark.asyncio
async def test_upload_custom_template_path_forwards_featured_image():
    # Matches selectCimCustomUploadTemplate's payload shape: template_id="classic" placeholder
    # + a real custom_template dict from /template/upload.
    custom_template = {"id": "custom-upload", "name": "My Upload", "file_ext": "pdf"}
    state = {
        **_BASE_STATE,
        "template_id": "classic",
        "custom_template": custom_template,
        "featured_image": FEATURED_IMAGE,
    }
    mock_gen = await _run(state)
    assert mock_gen.call_args.kwargs["featured_image"] == FEATURED_IMAGE
    assert mock_gen.call_args.kwargs["custom_template"] == custom_template


@pytest.mark.asyncio
async def test_no_image_selected_forwards_none_for_any_path():
    # User hit "Skip" in the image popup (or featured_image key absent entirely) — must not
    # crash and must not fabricate an image for generate_cim_html.
    state = {**_BASE_STATE, "template_id": "classic", "custom_template": None}
    mock_gen = await _run(state)
    assert mock_gen.call_args.kwargs["featured_image"] is None

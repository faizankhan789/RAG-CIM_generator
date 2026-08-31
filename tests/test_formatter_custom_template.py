"""Tests for nodes.formatter forwarding an uploaded custom_template into generate_cim_html.

Kept separate from tests/test_formatter.py, which currently fails to collect due to a
pre-existing, unrelated bug (it imports _merge_dicts/_merge_value from nodes.aggregator,
which no longer defines them) — not touched here since it's out of scope for this feature.
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


class TestFormatterCustomTemplate:
    @pytest.mark.asyncio
    async def test_forwards_custom_template_to_generate_cim_html(self):
        state = {
            "all_findings": ["some finding"],
            "all_images": [],
            "listing_xml": "",
            "listing_name": "Acme",
            "asking_price": "$1,000,000",
            "logo_url": "",
            "template_id": "classic",
            "custom_template": {"id": "custom-upload", "name": "My Upload"},
        }
        with patch("nodes.formatter.generate_cim_html", new=AsyncMock(return_value="<html></html>")) as mock_gen:
            result = await formatter_node(state)
        assert result["cim_output"] == "<html></html>"
        assert mock_gen.call_args.kwargs["custom_template"] == {"id": "custom-upload", "name": "My Upload"}

    @pytest.mark.asyncio
    async def test_defaults_custom_template_to_none(self):
        state = {
            "all_findings": ["some finding"],
            "all_images": [],
            "listing_xml": "",
            "listing_name": "Acme",
            "asking_price": "$1,000,000",
            "logo_url": "",
            "template_id": "classic",
        }
        with patch("nodes.formatter.generate_cim_html", new=AsyncMock(return_value="<html></html>")) as mock_gen:
            await formatter_node(state)
        assert mock_gen.call_args.kwargs["custom_template"] is None

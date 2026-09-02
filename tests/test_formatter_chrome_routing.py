"""Tests that nodes.formatter routes generate_cim_html's output correctly:
marker text (built-in templates) -> core.cim_assembler.assemble(); a full HTML
document (custom_template/PDF-upload) -> used as-is, assemble() never called.
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

MARKER_TEXT = (
    '<!-- INDUSTRY: Restaurant & Food Service -->\n'
    '<!-- SECTION num="I" title="Executive Summary" -->'
    '<p>Great business.</p>'
    '<!-- /SECTION -->'
)


class TestFormatterChromeRouting:
    @pytest.mark.asyncio
    async def test_builtin_template_routes_through_assembler(self):
        state = {
            "all_findings": ["some finding"],
            "all_images": [],
            "listing_xml": "",
            "listing_name": "O'Sarracino Pizzeria Ltd",
            "asking_price": "£450,000",
            "logo_url": "",
            "template_id": "classic",
        }
        with patch("nodes.formatter.generate_cim_html", new=AsyncMock(return_value=MARKER_TEXT)):
            result = await formatter_node(state)
        html = result["cim_output"]
        assert html.startswith("<!DOCTYPE html>")
        assert "O&#x27;Sarracino Pizzeria Ltd" in html
        assert "£450,000" in html
        assert "Great business." in html

    @pytest.mark.asyncio
    async def test_custom_template_bypasses_assembler_entirely(self):
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
        full_html = "<!DOCTYPE html><html><body>whatever Claude wrote, verbatim</body></html>"
        with patch("nodes.formatter.generate_cim_html", new=AsyncMock(return_value=full_html)), \
             patch("nodes.formatter.assemble") as mock_assemble:
            result = await formatter_node(state)
        mock_assemble.assert_not_called()
        assert result["cim_output"] == full_html

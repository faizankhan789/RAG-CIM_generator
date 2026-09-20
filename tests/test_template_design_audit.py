"""Tests for core.llm.audit_template_design — the dedicated design-audit
LLM call that runs once at template-upload time (see server.py's
/template/upload) to produce a much richer design spec than the
deterministic palette/font extraction. No real Claude API call is made —
core.llm.get_client is mocked throughout.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()  # core.llm reads CIM_MODEL at import time, same as server.py does

from core.llm import _build_template_directive, _format_design_audit, audit_template_design
from tests.pdf_helpers import make_text_pdf
from tests.template_helpers import make_text_docx, make_text_html

SAMPLE_AUDIT = {
    "cover": {
        "layout": "left-aligned",
        "background": "plain white",
        "decorative_elements": "none",
        "title_treatment": "large bold serif, left-aligned",
        "has_image": False,
    },
    "typography": {
        "heading_font_style": "bold serif",
        "body_font_style": "plain sans-serif",
        "heading_case": "title case",
        "letter_spacing": "normal",
    },
    "colors": {
        "primary_hex": "#1f4e79",
        "accent_hex": "#c9973a",
        "background_hex": "#ffffff",
        "mid_hex": "#5a5a5a",
        "notes": "gold used only for thin rule lines, never as a fill",
    },
    "section_headers": {
        "style": "plain with a thin rule beneath",
        "alignment": "left",
        "decoration": "none",
    },
    "body_style": {
        "density": "generous whitespace",
        "corner_style": "sharp corners",
        "shadows": "flat, no shadows",
        "table_style": "no visible borders, alternating row tint",
        "list_style": "simple dash bullets",
        "dividers": "thin horizontal rules between sections",
    },
    "distinctive_motifs": "a thin gold rule always sits directly beneath every heading",
}


def _fake_client(response_text: str, captured: dict | None = None):
    client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(text=response_text)]
    msg.usage = MagicMock(input_tokens=50, output_tokens=20)

    async def _create(**kwargs):
        if captured is not None:
            captured["messages"] = kwargs["messages"]
        return msg

    client.messages.create = _create
    return client


@pytest.mark.asyncio
async def test_returns_none_when_file_b64_empty():
    result = await audit_template_design("", "pdf")
    assert result is None


@pytest.mark.asyncio
async def test_parses_valid_json_response():
    with patch("core.llm.get_client", return_value=_fake_client(json.dumps(SAMPLE_AUDIT))):
        result = await audit_template_design(
            base64.standard_b64encode(make_text_pdf()).decode(), "pdf",
        )
    assert result == SAMPLE_AUDIT


@pytest.mark.asyncio
async def test_strips_markdown_fences_around_json():
    fenced = f"```json\n{json.dumps(SAMPLE_AUDIT)}\n```"
    with patch("core.llm.get_client", return_value=_fake_client(fenced)):
        result = await audit_template_design(
            base64.standard_b64encode(make_text_html()).decode(), "html",
        )
    assert result == SAMPLE_AUDIT


@pytest.mark.asyncio
async def test_invalid_json_returns_none_not_raise():
    with patch("core.llm.get_client", return_value=_fake_client("not valid json at all")):
        result = await audit_template_design(
            base64.standard_b64encode(make_text_docx()).decode(), "docx",
        )
    assert result is None


@pytest.mark.asyncio
async def test_non_object_json_returns_none():
    with patch("core.llm.get_client", return_value=_fake_client(json.dumps(["a", "list", "not", "a", "dict"]))):
        result = await audit_template_design(
            base64.standard_b64encode(make_text_pdf()).decode(), "pdf",
        )
    assert result is None


@pytest.mark.asyncio
async def test_api_exception_returns_none_not_raise():
    client = MagicMock()

    async def _raise(**kwargs):
        raise RuntimeError("API down")

    client.messages.create = _raise
    with patch("core.llm.get_client", return_value=client):
        result = await audit_template_design(
            base64.standard_b64encode(make_text_pdf()).decode(), "pdf",
        )
    assert result is None


@pytest.mark.asyncio
async def test_pdf_gets_document_block_same_as_generation_path():
    captured: dict = {}
    with patch("core.llm.get_client", return_value=_fake_client(json.dumps(SAMPLE_AUDIT), captured)):
        await audit_template_design(base64.standard_b64encode(make_text_pdf()).decode(), "pdf")
    content = captured["messages"][0]["content"]
    doc_blocks = [b for b in content if b.get("type") == "document"]
    assert len(doc_blocks) == 1
    assert doc_blocks[0]["source"]["media_type"] == "application/pdf"

    # The audit prompt itself must be present and instruct design-only, JSON-only output.
    audit_prompt_blocks = [b for b in content if "Return ONLY a single JSON object" in b.get("text", "")]
    assert len(audit_prompt_blocks) == 1


@pytest.mark.asyncio
async def test_never_describes_content_only_design_instruction_present():
    captured: dict = {}
    with patch("core.llm.get_client", return_value=_fake_client(json.dumps(SAMPLE_AUDIT), captured)):
        await audit_template_design(base64.standard_b64encode(make_text_html()).decode(), "html")
    content = captured["messages"][0]["content"]
    intro_blocks = [b for b in content if "VISUAL DESIGN ONLY" in b.get("text", "")]
    assert len(intro_blocks) == 1


class TestFormatDesignAudit:
    def test_full_audit_renders_all_sections(self):
        formatted = _format_design_audit(SAMPLE_AUDIT)
        assert "Cover:" in formatted
        assert "Typography:" in formatted
        assert "Section headers:" in formatted
        assert "Body style:" in formatted
        assert "Distinctive motifs:" in formatted
        assert "gold used only for thin rule lines" in formatted

    def test_empty_dict_renders_empty_string(self):
        assert _format_design_audit({}) == ""

    def test_partial_dict_does_not_crash(self):
        partial = {"cover": {"layout": "centered"}}
        formatted = _format_design_audit(partial)
        assert "centered" in formatted
        assert "Typography" not in formatted  # sections with no data are omitted, not shown empty

    def test_malformed_shape_does_not_crash(self):
        # A saved template's design_audit could theoretically be a weird shape
        # if something upstream ever changes — must degrade, never raise.
        malformed = {"cover": "not a dict", "colors": None}
        formatted = _format_design_audit(malformed)
        assert isinstance(formatted, str)

    def test_has_image_false_is_shown_not_silently_dropped(self):
        """Regression guard: has_image=False is real, meaningful data (the
        template's cover genuinely has no image) — a naive falsy-check would
        treat False the same as "field missing," losing that information."""
        formatted = _format_design_audit({"cover": {"layout": "centered", "has_image": False}})
        assert "has its own image=no" in formatted

    def test_has_image_true_is_shown(self):
        formatted = _format_design_audit({"cover": {"layout": "centered", "has_image": True}})
        assert "has its own image=yes" in formatted

    def test_has_image_missing_shows_na(self):
        formatted = _format_design_audit({"cover": {"layout": "centered"}})
        assert "has its own image=n/a" in formatted

    def test_motifs_of_none_is_omitted(self):
        audit = {**SAMPLE_AUDIT, "distinctive_motifs": "none"}
        formatted = _format_design_audit(audit)
        assert "Distinctive motifs" not in formatted


class TestBuildTemplateDirectiveWithAudit:
    _BASE_TEMPLATE = {
        "name": "Your Uploaded Template",
        "palette": {"primary": "#000000", "accent": "#000000", "light": "#ffffff", "mid": "#000000"},
        "fonts": {"heading": "serif", "body": "sans-serif"},
        "layout_notes": "",
        "cover_override": "",
        "section_header_override": "",
        "headings": {},
    }

    def test_includes_audit_block_when_present(self):
        template = {**self._BASE_TEMPLATE, "design_audit": SAMPLE_AUDIT}
        directive = _build_template_directive(template)
        assert "AUDITED DESIGN SPECIFICATION" in directive
        assert "SINGLE MOST" in directive
        assert "gold used only for thin rule lines" in directive

    def test_audit_colors_override_deterministic_palette_field_by_field(self):
        """The audit's own color estimate (vision-based, looking at the real
        rendered design) is more reliable than the deterministic extractor's
        heuristic — it must win in the actual injected COLOR PALETTE block,
        not just get mentioned in passing in the audit-summary text."""
        template = {**self._BASE_TEMPLATE, "design_audit": SAMPLE_AUDIT}
        directive = _build_template_directive(template)
        assert "- primary: #1f4e79" in directive
        assert "- accent:  #c9973a" in directive
        assert "- light:   #ffffff" in directive
        assert "- mid:     #5a5a5a" in directive
        # The base template's own deterministic black must NOT appear as
        # the primary/accent/mid value anywhere in the resolved palette.
        assert "- primary: #000000" not in directive
        assert "- mid:     #000000" not in directive

    def test_partial_audit_colors_fall_back_individually(self):
        """A partial audit (only some color fields given) must not discard
        the deterministic value for the fields it DIDN'T give — fallback is
        per-field, never all-or-nothing."""
        partial_audit = {"colors": {"primary_hex": "#1f4e79"}}  # only primary given
        template = {**self._BASE_TEMPLATE, "design_audit": partial_audit}
        directive = _build_template_directive(template)
        assert "- primary: #1f4e79" in directive   # audit's value used
        assert "- accent:  #000000" in directive   # deterministic fallback preserved
        assert "- mid:     #000000" in directive    # deterministic fallback preserved

    def test_invalid_hex_in_audit_falls_back_to_deterministic(self):
        """The audit prompt tells the LLM to write 'not visible'/'none' for
        inapplicable fields — a color field is nearly always applicable, but
        this guards against the LLM doing it anyway: garbage must never reach
        the actual injected palette."""
        bad_audit = {"colors": {"primary_hex": "not visible", "accent_hex": "gold-ish"}}
        template = {**self._BASE_TEMPLATE, "design_audit": bad_audit}
        directive = _build_template_directive(template)
        assert "- primary: #000000" in directive
        assert "- accent:  #000000" in directive

    def test_omits_audit_block_when_absent(self):
        """Backward compat: a template saved before this feature (or one
        whose audit call failed) has no design_audit key at all."""
        directive = _build_template_directive(dict(self._BASE_TEMPLATE))
        assert "AUDITED DESIGN SPECIFICATION" not in directive

    def test_omits_audit_block_when_none(self):
        template = {**self._BASE_TEMPLATE, "design_audit": None}
        directive = _build_template_directive(template)
        assert "AUDITED DESIGN SPECIFICATION" not in directive

    def test_omits_audit_block_when_empty_dict(self):
        template = {**self._BASE_TEMPLATE, "design_audit": {}}
        directive = _build_template_directive(template)
        assert "AUDITED DESIGN SPECIFICATION" not in directive

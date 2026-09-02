"""Parses the marker-delimited text the LLM emits for the 5 built-in
templates and renders it through the matching core/chrome/<id> skeleton.

Never touches the PDF-upload custom_template path — that one still returns
a full HTML document straight from core/llm.py and never reaches here.
"""

from __future__ import annotations

import json
import logging
import re

from core.chrome import get_palette, get_renderer
from core.chrome.components import RENDERERS as COMPONENT_RENDERERS, ComponentError

log = logging.getLogger(__name__)

_INDUSTRY_RE = re.compile(r"<!--\s*INDUSTRY:\s*(.*?)\s*-->")
_STATS_RE = re.compile(r"<!--\s*STATS\s*-->(.*?)<!--\s*/STATS\s*-->", re.DOTALL)
_SECTION_RE = re.compile(
    r"""<!--\s*SECTION\s+num=["'](?P<num>.*?)["']\s+title=["'](?P<title>.*?)["']\s*-->"""
    r"""(?P<body>.*?)"""
    r"""<!--\s*/SECTION\s*-->""",
    re.DOTALL,
)
_ANY_SECTION_OPEN_RE = re.compile(r"<!--\s*SECTION\b")
_C_RE = re.compile(r"<!--\s*C:(?P<type>[\w-]+)\s*-->(?P<payload>.*?)<!--\s*/C\s*-->", re.DOTALL)

_FALLBACK_INDUSTRY = "PRIVATE BUSINESS SALE"


def _build_logo_html(logo_b64: str, logo_mime: str) -> str:
    """Build the logo <img> tag from real fetched logo bytes — moved here from
    core/llm.py's old <!-- LOGO --> marker substitution. There's no marker to
    substitute anymore: the chrome renderer places this string directly in the
    cover top bar's left slot, so the old missing-marker fallback-placement bug
    class (logo colliding with a decorative frame) can no longer happen."""
    if not logo_b64 or not logo_mime:
        return ""
    return (
        f'<img src="data:{logo_mime};base64,{logo_b64}" alt="Company Logo" '
        f'style="max-height:60px;max-width:180px;object-fit:contain;display:block;"/>'
    )


def _parse_industry(marker_text: str) -> str:
    match = _INDUSTRY_RE.search(marker_text)
    if not match or not match.group(1).strip():
        log.error("cim_assembler: missing/malformed INDUSTRY marker, using fallback label")
        return _FALLBACK_INDUSTRY
    return match.group(1).strip()


def _parse_stats(marker_text: str) -> str:
    match = _STATS_RE.search(marker_text)
    return match.group(1).strip() if match else ""


def _parse_sections(marker_text: str) -> list[dict]:
    sections: list[dict] = []
    matched_spans: list[tuple[int, int]] = []
    for match in _SECTION_RE.finditer(marker_text):
        sections.append({
            "num": match.group("num").strip(),
            "title": match.group("title").strip(),
            "body_html": match.group("body").strip(),
        })
        matched_spans.append(match.span())

    # Any "<!-- SECTION" opener not covered by a well-formed match above is
    # malformed/unclosed — skip it, log it, keep the rest (spec: one bad
    # section must not fail the whole document).
    for open_match in _ANY_SECTION_OPEN_RE.finditer(marker_text):
        pos = open_match.start()
        if not any(start <= pos < end for start, end in matched_spans):
            log.error("cim_assembler: malformed/unclosed SECTION marker at offset %d, skipping", pos)

    return sections


def _render_components(body_html: str, palette: dict) -> str:
    """Replace each `<!-- C:type -->{JSON}<!-- /C -->` block with its rendered
    component HTML. A malformed block (bad JSON, unknown type, invalid/missing
    fields) is dropped and logged — never retried, never fails the section —
    matching the existing malformed-SECTION-marker behavior above."""

    def _replace(match: re.Match) -> str:
        c_type = match.group("type").strip()
        raw_payload = match.group("payload").strip()
        renderer = COMPONENT_RENDERERS.get(c_type)
        if renderer is None:
            log.error("cim_assembler: unknown component type %r, skipping block", c_type)
            return ""
        try:
            payload = json.loads(raw_payload)
            return renderer(payload, palette)
        except (json.JSONDecodeError, ComponentError) as exc:
            log.error("cim_assembler: malformed C:%s block, skipping (%s)", c_type, exc)
            return ""

    return _C_RE.sub(_replace, body_html)


def assemble(marker_text: str, template_id: str, business_name: str, asking_price: str,
             date_str: str, logo_b64: str = "", logo_mime: str = "",
             brand_primary: str = "", brand_accent: str = "") -> str:
    """Parse marker_text and render it through the template's chrome skeleton.

    Falls back to returning marker_text unchanged if no SECTION markers are
    found at all (contract violation — old-style full HTML leaked through,
    or a malformed response) rather than emitting a broken half-assembled
    document.

    brand_primary/brand_accent are real data (extracted from the fetched logo,
    same as today) — only the "classic" template's chrome uses them (matches
    core/templates.py's allow_brand_override flag); every other template
    absorbs and ignores them via **_ignored.
    """
    sections = _parse_sections(marker_text)
    if not sections:
        log.error(
            "cim_assembler: no SECTION markers found for template_id=%s, "
            "falling back to raw LLM text",
            template_id,
        )
        return marker_text

    industry = _parse_industry(marker_text)
    palette = get_palette(template_id, industry, brand_primary, brand_accent)
    stats_html = _render_components(_parse_stats(marker_text), palette)
    logo_html = _build_logo_html(logo_b64, logo_mime)
    for section in sections:
        section["body_html"] = _render_components(section["body_html"], palette)
    renderer = get_renderer(template_id)
    return renderer(
        business_name=business_name,
        asking_price=asking_price,
        industry=industry,
        date_str=date_str,
        logo_html=logo_html,
        stats_html=stats_html,
        sections=sections,
        brand_primary=brand_primary,
        brand_accent=brand_accent,
    )

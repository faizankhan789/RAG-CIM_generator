"""Deterministic PDF -> CIM template-style extractor. No LLM involved.

Given an uploaded PDF, measures its fonts/colors/layout and produces a plain
dict shaped exactly like one entry of core/templates.py's TEMPLATES — so it
can be passed straight into core/llm.py:generate_cim_html(custom_template=...)
with no other code changes needed downstream.
"""

from __future__ import annotations

from collections import Counter

import pymupdf as fitz

from core.section_matcher import CANONICAL_SECTIONS, match_sections

_FONT_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("times", "georgia", "garamond", "minion"), "Georgia, 'Times New Roman', Times, serif"),
    (("helvetica", "arial", "segoe"), "'Helvetica Neue', Helvetica, Arial, sans-serif"),
    (("futura", "century gothic", "gothic"), "'Century Gothic', Futura, 'Trebuchet MS', sans-serif"),
    (("courier", "mono"), "'Courier New', Courier, monospace"),
]

_DEFAULT_SANS = "'Helvetica Neue', Helvetica, Arial, sans-serif"
_DEFAULT_SERIF = "Georgia, 'Times New Roman', Times, serif"

_BULLET_GLYPHS = ("•", "◦", "▪", "-", "–", "✓")


def _hex_color(color_int: int) -> str:
    """Convert PyMuPDF's packed sRGB int (0xRRGGBB) to a CSS hex string."""
    return "#%06x" % (color_int & 0xFFFFFF)


def _font_stack_for(font_name: str, is_serif_flag: bool) -> str:
    """Map a PDF-embedded font name to a safe CSS font-stack, deterministically."""
    name = (font_name or "").lower()
    for keywords, stack in _FONT_KEYWORDS:
        if any(k in name for k in keywords):
            return stack
    return _DEFAULT_SERIF if is_serif_flag else _DEFAULT_SANS


def _detect_bullet_glyph(texts: list[str]) -> str:
    """Return the most common leading bullet glyph across a list of line texts."""
    counts: Counter[str] = Counter()
    for text in texts:
        stripped = text.strip()
        if stripped and stripped[0] in _BULLET_GLYPHS:
            counts[stripped[0]] += 1
    return counts.most_common(1)[0][0] if counts else "•"


def _detect_alignment(bbox: tuple[float, float, float, float], page_width: float) -> str:
    """Classify a text span as 'center' or 'left' aligned relative to the page."""
    span_center = (bbox[0] + bbox[2]) / 2
    return "center" if abs(span_center - page_width / 2) < page_width * 0.1 else "left"


class NoExtractableTextError(ValueError):
    """Raised when the uploaded PDF has no selectable text (e.g. a scanned image)."""


def _iter_spans(doc: "fitz.Document"):
    """Yield every text span dict across the whole document, in document order."""
    for page in doc:
        raw = page.get_text("dict")
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("text", "").strip():
                        yield span


def _band_color(doc: "fitz.Document") -> str | None:
    """Most common non-black/white filled shape color across the document, if any."""
    fill_colors: Counter[str] = Counter()
    for page in doc:
        for drawing in page.get_drawings():
            fill = drawing.get("fill")
            if fill is not None:
                r, g, b = fill[:3]
                fill_colors[f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"] += 1
    for color, _count in fill_colors.most_common():
        if color not in ("#ffffff", "#000000"):
            return color
    return None


def extract_style_profile(pdf_bytes: bytes) -> tuple[dict, list[str]]:
    """Extract a CIM template-style dict from an uploaded PDF. Deterministic, no LLM.

    Returns (template_dict, warnings). template_dict matches the shape of
    core/templates.py TEMPLATES[id]. warnings lists canonical CIM sections that
    had no matching heading in the PDF — those fall back to the classic
    template's default wording rather than being silently dropped.

    Raises NoExtractableTextError if the PDF has no selectable text layer.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    all_spans = list(_iter_spans(doc))
    if not all_spans:
        raise NoExtractableTextError(
            "This PDF has no selectable text — we can't read its layout. Try a text-based PDF."
        )

    body_size = Counter(round(span["size"]) for span in all_spans).most_common(1)[0][0]
    heading_spans = [s for s in all_spans if s["size"] >= body_size * 1.15]
    body_spans = [s for s in all_spans if s["size"] < body_size * 1.15]
    if not heading_spans:
        heading_spans, body_spans = all_spans[:1], all_spans[1:]

    heading_colors = Counter(_hex_color(s["color"]) for s in heading_spans)
    body_colors = Counter(_hex_color(s["color"]) for s in body_spans) or heading_colors
    primary = heading_colors.most_common(1)[0][0]
    mid = body_colors.most_common(1)[0][0]

    band = _band_color(doc)
    accent = band or (mid if mid != primary else "#3a6ea5")
    light = "#ffffff"

    heading_font, body_font = heading_spans[0], (body_spans[0] if body_spans else heading_spans[0])
    fonts = {
        "heading": _font_stack_for(heading_font["font"], bool(heading_font["flags"] & 4)),
        "body": _font_stack_for(body_font["font"], bool(body_font["flags"] & 4)),
    }

    bullet_glyph = _detect_bullet_glyph([s["text"] for s in body_spans])

    top_heading = min(heading_spans, key=lambda s: s["bbox"][1])
    page_width = doc[0].rect.width
    alignment = _detect_alignment(top_heading["bbox"], page_width)

    matched = match_sections([s["text"].strip() for s in heading_spans])
    headings = {h: h for h in CANONICAL_SECTIONS}
    headings.update(matched)
    warnings = [h for h in CANONICAL_SECTIONS if h not in matched]

    band_sentence = (
        f"Section headers sit on a solid {band} background band."
        if band else
        "Section headers use a plain background with a thin rule beneath the title."
    )
    layout_notes = (
        f"{band_sentence} Bullet lists use the '{bullet_glyph}' glyph as the marker, "
        "matching the uploaded template."
    )
    align_sentence = (
        "Cover content is centered, matching the uploaded template's title page."
        if alignment == "center" else
        "Cover content is left-aligned, matching the uploaded template's title page."
    )
    cover_override = align_sentence + (f" The title sits on a {band} background band." if band else "")
    section_header_override = (
        f"Section header band uses {band} as the background color, matching the uploaded template."
        if band else
        "Section headers use a plain background with a rule beneath the title, matching the uploaded template."
    )

    doc.close()
    template = {
        "id": "custom-upload",
        "name": "Your Uploaded Template",
        "tagline": "Matches the design of your uploaded PDF.",
        "allow_brand_override": False,
        "palette": {"primary": primary, "accent": accent, "light": light, "mid": mid},
        "fonts": fonts,
        "layout_notes": layout_notes,
        "cover_override": cover_override,
        "section_header_override": section_header_override,
        "headings": headings,
    }
    return template, warnings

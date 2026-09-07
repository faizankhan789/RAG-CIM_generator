"""Deterministic HTML/XML -> CIM template-style extractor. No LLM involved.

Markup counterpart to core/pdf_style_extractor.py — same output contract
(see that module's docstring). HTML/XML carry no page geometry (no bbox, no
fixed point size), so this is more heuristic than the PDF/DOCX extractors:
it regex-scans inline `style="..."` attributes and `<style>` block CSS text
for color/font-family/text-align declarations rather than resolving the
full CSS cascade (inherited styles, specificity, external stylesheets are
all invisible to this). Good enough to seed a template's palette and fonts;
not a layout engine. Deliberately stdlib-only (`re`) — no new dependency for
what's inherently a best-effort heuristic.
"""

from __future__ import annotations

import re
from collections import Counter

from core.pdf_style_extractor import NoExtractableTextError
from core.section_matcher import CANONICAL_SECTIONS, match_sections
from core.style_shared import DEFAULT_SANS, detect_bullet_glyph, font_stack_for

_HEADING_TAG_RE = re.compile(r"<(h1|h2|h3|title)\b([^>]*)>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_BODY_TAG_RE = re.compile(r"<(p|li|td|div|span)\b([^>]*)>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
_CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_TAG_RE = re.compile(r"<[^>]+>")
_COLOR_DECL_RE = re.compile(r"(?<!background-)(?<!-)color\s*:\s*(#[0-9a-fA-F]{3,6}|rgb\([^)]+\))")
_BG_DECL_RE = re.compile(r"background(?:-color)?\s*:\s*(#[0-9a-fA-F]{3,6}|rgb\([^)]+\))")
_FONT_DECL_RE = re.compile(r"font-family\s*:\s*([^;\"']+)")
_CENTER_RE = re.compile(r"text-align\s*:\s*center")
_RGB_RE = re.compile(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text).strip()


def _normalize_color(raw: str) -> str | None:
    raw = raw.strip()
    if raw.startswith("#"):
        hexpart = raw[1:]
        if len(hexpart) == 3:
            hexpart = "".join(c * 2 for c in hexpart)
        return f"#{hexpart.lower()}" if len(hexpart) == 6 else None
    m = _RGB_RE.match(raw)
    if m:
        r, g, b = (int(x) for x in m.groups())
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


def _colors_in(text: str, decl_re: "re.Pattern") -> list[str]:
    return [c for c in (_normalize_color(m) for m in decl_re.findall(text)) if c]


def _fonts_in(text: str) -> list[str]:
    return [m.strip().strip("'\"") for m in _FONT_DECL_RE.findall(text)]


def _css_declarations_for(css: str, tags: tuple[str, ...]) -> str:
    """Concatenate the declaration bodies of every CSS rule whose selector list
    mentions any of the given tag/class names — a bare name match only (strips
    one leading '.'/'#'), no specificity/cascade resolution. Without this,
    heading vs. body CSS (almost always in a <style> block, not inline) can't
    be told apart, and one silently falls back to the other's declarations."""
    tagset = set(tags)
    parts = []
    for selectors, decls in _CSS_RULE_RE.findall(css):
        sel_names = {s.strip().lstrip(".#").split(":")[0].lower() for s in selectors.split(",")}
        if sel_names & tagset:
            parts.append(decls)
    return "\n".join(parts)


_HEADING_SELECTORS = ("h1", "h2", "h3", "title", "header", "hero", "cover")
_BODY_SELECTORS = ("p", "li", "td", "div", "span", "body")
_BAND_SELECTORS = ("header", "nav", "thead", "th", "hero", "cover", "band")


def extract_style_profile(markup_bytes: bytes) -> tuple[dict, list[str]]:
    """Extract a CIM template-style dict from an uploaded HTML or XML file.
    Deterministic, no LLM. Same return contract as
    core/pdf_style_extractor.py:extract_style_profile.

    Raises NoExtractableTextError if the file has no readable text content at all
    (a genuinely empty file, or one this can't parse any text out of).
    """
    raw = markup_bytes.decode("utf-8", errors="ignore")

    heading_matches = _HEADING_TAG_RE.findall(raw)
    body_matches = _BODY_TAG_RE.findall(raw)
    heading_texts = [t for t in (_strip_tags(body) for _, _, body in heading_matches) if t]
    body_texts = [t for t in (_strip_tags(body) for _, _, body in body_matches) if t]

    if not heading_texts and not body_texts:
        # No recognized tags (h1/h2/h3/title/p/li/td/div/span) — could still be a
        # custom XML schema with real text in unrecognized tags. Fall back to a
        # flat strip-all-tags pass rather than failing a non-empty file outright.
        fallback_text = _strip_tags(raw)
        if not fallback_text:
            raise NoExtractableTextError(
                "This file has no readable text content — we can't read its layout."
            )
        body_texts = [fallback_text]

    global_css = "\n".join(_STYLE_BLOCK_RE.findall(raw))
    heading_css = _css_declarations_for(global_css, _HEADING_SELECTORS)
    body_css = _css_declarations_for(global_css, _BODY_SELECTORS)
    band_css = _css_declarations_for(global_css, _BAND_SELECTORS)
    heading_style_attrs = [attrs for _, attrs, _ in heading_matches]
    body_style_attrs = [attrs for _, attrs, _ in body_matches]

    heading_colors = Counter(
        c for attrs in heading_style_attrs for c in _colors_in(attrs, _COLOR_DECL_RE)
    ) or Counter(_colors_in(heading_css, _COLOR_DECL_RE))
    body_colors = Counter(
        c for attrs in body_style_attrs for c in _colors_in(attrs, _COLOR_DECL_RE)
    ) or Counter(_colors_in(body_css, _COLOR_DECL_RE)) or heading_colors

    primary = heading_colors.most_common(1)[0][0] if heading_colors else "#000000"
    mid = body_colors.most_common(1)[0][0] if body_colors else primary

    band_colors = Counter(
        c for attrs in heading_style_attrs for c in _colors_in(attrs, _BG_DECL_RE)
    ) or Counter(_colors_in(band_css, _BG_DECL_RE)) or Counter(_colors_in(global_css, _BG_DECL_RE))
    band = next((c for c, _n in band_colors.most_common() if c not in ("#ffffff", "#000000")), None)
    accent = band or (mid if mid != primary else "#3a6ea5")
    light = "#ffffff"

    heading_fonts = [f for attrs in heading_style_attrs for f in _fonts_in(attrs)] or _fonts_in(heading_css)
    body_fonts = (
        [f for attrs in body_style_attrs for f in _fonts_in(attrs)]
        or _fonts_in(body_css)
        or heading_fonts
    )
    fonts = {
        "heading": font_stack_for(heading_fonts[0]) if heading_fonts else DEFAULT_SANS,
        "body": font_stack_for(body_fonts[0]) if body_fonts else DEFAULT_SANS,
    }

    bullet_glyph = detect_bullet_glyph(body_texts)

    first_heading_style = heading_style_attrs[0] if heading_style_attrs else ""
    alignment = "center" if _CENTER_RE.search(first_heading_style) or _CENTER_RE.search(heading_css) else "left"

    matched = match_sections(heading_texts)
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

    template = {
        "id": "custom-upload",
        "name": "Your Uploaded Template",
        "tagline": "Matches the design of your uploaded HTML/XML template.",
        "allow_brand_override": False,
        "palette": {"primary": primary, "accent": accent, "light": light, "mid": mid},
        "fonts": fonts,
        "layout_notes": layout_notes,
        "cover_override": cover_override,
        "section_header_override": section_header_override,
        "headings": headings,
    }
    return template, warnings

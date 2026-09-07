"""Shared low-level helpers for the per-format template-style extractors
(core/pdf_style_extractor.py, core/docx_style_extractor.py,
core/markup_style_extractor.py). Font-name -> CSS stack mapping and bullet
glyph detection are format-agnostic, so they live here once instead of
being copied per format.
"""

from __future__ import annotations

from collections import Counter

_FONT_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("times", "georgia", "garamond", "minion"), "Georgia, 'Times New Roman', Times, serif"),
    (("helvetica", "arial", "segoe"), "'Helvetica Neue', Helvetica, Arial, sans-serif"),
    (("futura", "century gothic", "gothic"), "'Century Gothic', Futura, 'Trebuchet MS', sans-serif"),
    (("courier", "mono"), "'Courier New', Courier, monospace"),
]

DEFAULT_SANS = "'Helvetica Neue', Helvetica, Arial, sans-serif"
DEFAULT_SERIF = "Georgia, 'Times New Roman', Times, serif"

BULLET_GLYPHS = ("•", "◦", "▪", "-", "–", "✓")


def font_stack_for(font_name: str, is_serif_flag: bool = False) -> str:
    """Map a source-document font name to a safe CSS font-stack, deterministically."""
    name = (font_name or "").lower()
    for keywords, stack in _FONT_KEYWORDS:
        if any(k in name for k in keywords):
            return stack
    return DEFAULT_SERIF if is_serif_flag else DEFAULT_SANS


def detect_bullet_glyph(texts: list[str]) -> str:
    """Return the most common leading bullet glyph across a list of line texts."""
    counts: Counter[str] = Counter()
    for text in texts:
        stripped = text.strip()
        if stripped and stripped[0] in BULLET_GLYPHS:
            counts[stripped[0]] += 1
    return counts.most_common(1)[0][0] if counts else "•"

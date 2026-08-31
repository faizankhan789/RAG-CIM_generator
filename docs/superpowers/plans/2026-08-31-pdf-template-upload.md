# PDF Custom Template Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user upload a PDF and generate a CIM that visually matches it (colors, fonts, section layout, icons), with real listing data, using deterministic PDF parsing — no LLM involved in the style analysis.

**Architecture:** A new extractor turns an uploaded PDF into a plain dict shaped exactly like an entry of `core/templates.py`'s `TEMPLATES` (palette/fonts/layout_notes/cover_override/section_header_override/headings). That dict flows through the existing template-driven generation pipeline unchanged — `core/llm.py:generate_cim_html()` gets one new optional parameter (`custom_template`) that wins over `template_id` when set. No new rendering path, no LLM used for style detection; Claude still writes the actual CIM content, same as the 5 built-in templates today.

**Tech Stack:** Python, FastAPI, PyMuPDF (`fitz`) for deterministic PDF parsing, pytest for tests, PHP/Yii for the CRM-side upload UI.

**Spec:** `docs/superpowers/specs/2026-08-31-pdf-template-upload-design.md`

## Global Constraints

- Scope is **PDF only** for this plan — DOC/DOCX and HTML uploads are separate future phases, not touched here.
- Style/layout extraction (colors, fonts, section structure, icons) must be fully deterministic — **no LLM calls** anywhere in the extraction path.
- CIM content generation (the actual prose Claude writes per section) is unchanged — same Claude call, same rules, as the 5 existing templates.
- No OCR in v1 — a PDF with no extractable text layer is a rejected upload, not a fallback path.
- No persistent template library in v1 — the extracted template dict flows through the current request/session only; it is not saved server-side for reuse.
- **Do not run any git commands (`git add`/`commit`/`push`) at any point in this plan — the user manages git themselves.** Skip the "Commit" step that normally ends each task below; every task ends at its last passing test run instead.

---

### Task 1: `core/section_matcher.py` — canonical CIM taxonomy matching

**Files:**
- Create: `core/section_matcher.py`
- Test: `tests/test_section_matcher.py`

**Interfaces:**
- Produces: `CANONICAL_SECTIONS: list[str]` (the 10 standard CIM heading strings, e.g. `"I. Executive Summary"`), `match_sections(detected_headings: list[str]) -> dict[str, str]` (canonical heading → the PDF's own label text, only for sections that matched).

- [x] **Step 1: Write the failing tests**

```python
"""Tests for core.section_matcher — deterministic heading-to-CIM-taxonomy matching."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.section_matcher import CANONICAL_SECTIONS, match_sections


def test_exact_canonical_text_matches():
    result = match_sections(["I. Executive Summary"])
    assert result["I. Executive Summary"] == "I. Executive Summary"


def test_synonym_matches_and_keeps_original_label():
    result = match_sections(["Financials"])
    assert result["III. Financial Information"] == "Financials"


def test_numbered_and_punctuated_prefixes_stripped():
    result = match_sections(["3. Financial Overview"])
    assert result["III. Financial Information"] == "3. Financial Overview"


def test_case_insensitive():
    result = match_sections(["EXECUTIVE SUMMARY"])
    assert result["I. Executive Summary"] == "EXECUTIVE SUMMARY"


def test_unmatched_heading_is_dropped_not_guessed():
    result = match_sections(["Table of Contents", "Our Awesome Team Photos"])
    assert result == {}


def test_first_match_wins_on_duplicate_canonical_target():
    result = match_sections(["Executive Summary", "Summary"])
    assert result["I. Executive Summary"] == "Executive Summary"


def test_all_ten_canonical_sections_present():
    assert len(CANONICAL_SECTIONS) == 10
    assert CANONICAL_SECTIONS[0] == "I. Executive Summary"
    assert CANONICAL_SECTIONS[-1] == "X. Appendix"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd cim-generator && python -m pytest tests/test_section_matcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.section_matcher'`

- [x] **Step 3: Write the implementation**

```python
"""Deterministic matching of PDF heading text to the canonical CIM section taxonomy.

No LLM — this is a fixed lookup table plus light text normalization. Mirrors the
10-section structure core/llm.py and core/templates.py already treat as standard.
"""

from __future__ import annotations

import re

CANONICAL_SECTIONS: list[str] = [
    "I. Executive Summary",
    "II. Company Overview",
    "III. Financial Information",
    "IV. Operations",
    "V. Marketing and Sales",
    "VI. Legal and Regulatory",
    "VII. Human Resources",
    "VIII. Growth Opportunities",
    "IX. Risks",
    "X. Appendix",
]

_SYNONYMS: dict[str, str] = {
    "executive summary": "I. Executive Summary",
    "summary": "I. Executive Summary",
    "company overview": "II. Company Overview",
    "overview": "II. Company Overview",
    "about us": "II. Company Overview",
    "the business": "II. Company Overview",
    "financial information": "III. Financial Information",
    "financials": "III. Financial Information",
    "financial overview": "III. Financial Information",
    "the numbers": "III. Financial Information",
    "operations": "IV. Operations",
    "marketing and sales": "V. Marketing and Sales",
    "marketing & sales": "V. Marketing and Sales",
    "sales and marketing": "V. Marketing and Sales",
    "legal and regulatory": "VI. Legal and Regulatory",
    "legal": "VI. Legal and Regulatory",
    "human resources": "VII. Human Resources",
    "hr": "VII. Human Resources",
    "the team": "VII. Human Resources",
    "growth opportunities": "VIII. Growth Opportunities",
    "growth": "VIII. Growth Opportunities",
    "risks": "IX. Risks",
    "risk factors": "IX. Risks",
    "appendix": "X. Appendix",
}

# Every canonical heading also matches its own exact (normalized) text.
for _heading in CANONICAL_SECTIONS:
    _key = _heading.split(". ", 1)[1].lower()
    _SYNONYMS.setdefault(_key, _heading)


def _normalize(text: str) -> str:
    """Strip roman-numeral/number prefixes and punctuation, lowercase, collapse spaces."""
    text = re.sub(r"^[IVXLCDM]+\.\s*", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"^\d+[\.\)]\s*", "", text)
    text = re.sub(r"[^a-z0-9 &]", "", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def match_sections(detected_headings: list[str]) -> dict[str, str]:
    """Map canonical CIM sections to the uploaded PDF's own heading label, where found.

    Returns a dict shaped like core/templates.py TEMPLATES["classic"]["headings"]:
    canonical heading -> display label taken verbatim from the PDF. Canonical
    sections with no match are simply absent from the result — callers (see
    core/pdf_style_extractor.py) fall back to the classic template's own wording
    for those, and surface a warning rather than failing silently.
    """
    result: dict[str, str] = {}
    for raw in detected_headings:
        canonical = _SYNONYMS.get(_normalize(raw))
        if canonical and canonical not in result:
            result[canonical] = raw.strip()
    return result
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd cim-generator && python -m pytest tests/test_section_matcher.py -v`
Expected: PASS (7 passed)

---

### Task 2: `core/pdf_style_extractor.py` — pure style-detail helpers

**Files:**
- Create: `core/pdf_style_extractor.py`
- Test: `tests/test_pdf_style_extractor.py`

**Interfaces:**
- Consumes: nothing external yet (pure functions, stdlib only).
- Produces: `_hex_color(color_int: int) -> str`, `_font_stack_for(font_name: str, is_serif_flag: bool) -> str`, `_detect_bullet_glyph(texts: list[str]) -> str`, `_detect_alignment(bbox: tuple[float, float, float, float], page_width: float) -> str`. These are consumed by Task 3.

- [x] **Step 1: Write the failing tests**

```python
"""Tests for core.pdf_style_extractor's pure helper functions (no PDF parsing yet)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.pdf_style_extractor import (
    _detect_alignment,
    _detect_bullet_glyph,
    _font_stack_for,
    _hex_color,
)


def test_hex_color_from_packed_int():
    assert _hex_color(0x0000FF) == "#0000ff"
    assert _hex_color(0x000000) == "#000000"


def test_font_stack_matches_known_family():
    assert "Helvetica" in _font_stack_for("Helvetica-Bold", is_serif_flag=False)
    assert "Georgia" in _font_stack_for("TimesNewRomanPSMT", is_serif_flag=True)


def test_font_stack_falls_back_on_serif_flag():
    stack = _font_stack_for("SomeCustomFontXYZ", is_serif_flag=True)
    assert "Georgia" in stack or "serif" in stack


def test_font_stack_falls_back_to_sans_by_default():
    stack = _font_stack_for("SomeCustomFontXYZ", is_serif_flag=False)
    assert "Helvetica" in stack or "sans-serif" in stack


def test_bullet_glyph_picks_most_common():
    assert _detect_bullet_glyph(["• one", "• two", "- three"]) == "•"


def test_bullet_glyph_defaults_when_none_found():
    assert _detect_bullet_glyph(["no bullets here", "or here"]) == "•"


def test_alignment_centered():
    # page width 600, span centered around x=250-350 -> center 300 == page_width/2
    assert _detect_alignment((250, 0, 350, 20), page_width=600) == "center"


def test_alignment_left():
    # span sits near the left edge, far from page center
    assert _detect_alignment((20, 0, 120, 20), page_width=600) == "left"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd cim-generator && python -m pytest tests/test_pdf_style_extractor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.pdf_style_extractor'`

- [x] **Step 3: Write the implementation**

```python
"""Deterministic PDF -> CIM template-style extractor. No LLM involved.

Given an uploaded PDF, measures its fonts/colors/layout and produces a plain
dict shaped exactly like one entry of core/templates.py's TEMPLATES — so it
can be passed straight into core/llm.py:generate_cim_html(custom_template=...)
with no other code changes needed downstream.
"""

from __future__ import annotations

from collections import Counter

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
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd cim-generator && python -m pytest tests/test_pdf_style_extractor.py -v`
Expected: PASS (8 passed)

---

### Task 3: `core/pdf_style_extractor.py` — full PDF parsing + `extract_style_profile`

**Files:**
- Modify: `requirements.txt` (add PyMuPDF)
- Modify: `core/pdf_style_extractor.py` (append to Task 2's file)
- Create: `tests/pdf_helpers.py` (shared in-memory PDF builders for tests)
- Modify: `tests/test_pdf_style_extractor.py` (append integration tests)

**Interfaces:**
- Consumes: `core.section_matcher.CANONICAL_SECTIONS`, `core.section_matcher.match_sections` (Task 1); `_hex_color`, `_font_stack_for`, `_detect_bullet_glyph`, `_detect_alignment` (Task 2, same file).
- Produces: `NoExtractableTextError(ValueError)`, `extract_style_profile(pdf_bytes: bytes) -> tuple[dict, list[str]]` — consumed by Task 6's upload endpoint. The returned dict has exactly the keys `id, name, tagline, allow_brand_override, palette{primary,accent,light,mid}, fonts{heading,body}, layout_notes, cover_override, section_header_override, headings` — the same shape `core/llm.py:_select_template` (Task 4) already expects for `custom_template`.

- [x] **Step 1: Add the PyMuPDF dependency**

In `requirements.txt`, add a new section:

```
# PDF layout/style extraction for uploaded custom templates
PyMuPDF>=1.24.0
```

- [x] **Step 2: Install it**

Run: `cd cim-generator && pip install -r requirements.txt`
Expected: PyMuPDF installs (or is already satisfied)

- [x] **Step 3: Write the shared PDF test-fixture helpers**

```python
"""Shared helpers for building small in-memory PDFs for extractor/endpoint tests.

Built with PyMuPDF itself so tests are fully offline and don't need binary
fixture files committed to the repo.
"""

from __future__ import annotations

import fitz


def make_text_pdf(
    heading: str = "I. Executive Summary",
    body: str = "Some body copy about the business.",
) -> bytes:
    """A single-page PDF with one heading-sized span and one body-sized span."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), heading, fontsize=24, fontname="Helvetica-Bold", color=(0, 0, 1))
    page.insert_text((50, 110), body, fontsize=11, fontname="Helvetica", color=(0, 0, 0))
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def make_text_pdf_with_band(fill_rgb: tuple[float, float, float] = (1, 0.6, 0)) -> bytes:
    """A single-page PDF with a filled band behind the heading, for band-color detection."""
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(0, 60, 595, 100), color=None, fill=fill_rgb)
    page.insert_text((50, 85), "I. Executive Summary", fontsize=24, fontname="Helvetica-Bold", color=(0, 0, 1))
    page.insert_text((50, 130), "Some body copy about the business.", fontsize=11, fontname="Helvetica", color=(0, 0, 0))
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def make_no_text_pdf() -> bytes:
    """A page with a shape but zero text spans — simulates a scanned/image-only page."""
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(10, 10, 100, 100), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes
```

- [x] **Step 4: Write the failing integration tests**

Append to `tests/test_pdf_style_extractor.py`:

```python
import pytest

from core.pdf_style_extractor import NoExtractableTextError, extract_style_profile
from tests.pdf_helpers import make_no_text_pdf, make_text_pdf, make_text_pdf_with_band


def test_extract_raises_on_no_text_layer():
    with pytest.raises(NoExtractableTextError):
        extract_style_profile(make_no_text_pdf())


def test_extract_basic_pdf_shape_and_values():
    template, warnings = extract_style_profile(make_text_pdf())
    assert template["id"] == "custom-upload"
    assert template["palette"]["primary"] == "#0000ff"   # heading color
    assert template["palette"]["mid"] == "#000000"        # body color
    assert "Helvetica" in template["fonts"]["heading"]
    assert template["headings"]["I. Executive Summary"] == "I. Executive Summary"
    # Only one heading was in the PDF -> the other 9 canonical sections are unmatched
    assert len(warnings) == 9
    assert "I. Executive Summary" not in warnings


def test_extract_detects_band_color():
    template, _warnings = extract_style_profile(make_text_pdf_with_band((1, 0.6, 0)))
    assert template["palette"]["accent"] == "#ff9900"
    assert "#ff9900" in template["section_header_override"]
```

- [x] **Step 5: Run tests to verify they fail**

Run: `cd cim-generator && python -m pytest tests/test_pdf_style_extractor.py -v`
Expected: FAIL with `ImportError: cannot import name 'NoExtractableTextError'`

- [x] **Step 6: Write the implementation**

Append to `core/pdf_style_extractor.py`:

```python
import fitz  # PyMuPDF

from core.section_matcher import CANONICAL_SECTIONS, match_sections


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
```

- [x] **Step 7: Run tests to verify they pass**

Run: `cd cim-generator && python -m pytest tests/test_pdf_style_extractor.py -v`
Expected: PASS (11 passed total in this file)

---

### Task 4: `core/llm.py` — accept a custom template dict

**Files:**
- Modify: `core/llm.py:794-809` (the `generate_cim_html` signature and its `template = get_template(template_id)` line)
- Test: `tests/test_llm_template_selection.py`

**Interfaces:**
- Consumes: `core.templates.get_template` (already imported in `core/llm.py`).
- Produces: `_select_template(template_id: str, custom_template: dict | None) -> dict`; `generate_cim_html(..., custom_template: dict | None = None)` — consumed by Task 5's `formatter_node`.

- [x] **Step 1: Write the failing test**

```python
"""Tests for core.llm's template-selection logic (no Claude API calls needed)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.llm import _select_template
from core.templates import TEMPLATES


def test_custom_template_wins_over_template_id():
    custom = {"id": "custom-upload", "name": "My Upload"}
    assert _select_template("classic", custom) == custom


def test_falls_back_to_template_id_when_no_custom():
    assert _select_template("minimalist", None) == TEMPLATES["minimalist"]


def test_falls_back_to_classic_on_unknown_template_id():
    assert _select_template("nonsense", None) == TEMPLATES["classic"]
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd cim-generator && python -m pytest tests/test_llm_template_selection.py -v`
Expected: FAIL with `ImportError: cannot import name '_select_template'`

- [x] **Step 3: Implement**

In `core/llm.py`, immediately before `def _build_template_directive(template: dict) -> str:` (line 731), add:

```python
def _select_template(template_id: str, custom_template: dict | None) -> dict:
    """Pick the active template dict — an uploaded custom template always wins."""
    return custom_template if custom_template else get_template(template_id)
```

Then change the `generate_cim_html` signature (line 794-805) from:

```python
async def generate_cim_html(
    all_findings: list[str],
    listing_xml: str,
    listing_name: str,
    asking_price: str,
    all_images: list[dict] | None = None,
    logo_b64: str = "",
    logo_mime: str = "",
    brand_primary: str = "",
    brand_accent: str = "",
    template_id: str = "classic",
) -> str:
```

to:

```python
async def generate_cim_html(
    all_findings: list[str],
    listing_xml: str,
    listing_name: str,
    asking_price: str,
    all_images: list[dict] | None = None,
    logo_b64: str = "",
    logo_mime: str = "",
    brand_primary: str = "",
    brand_accent: str = "",
    template_id: str = "classic",
    custom_template: dict | None = None,
) -> str:
```

And change line 809 from:

```python
    template = get_template(template_id)
```

to:

```python
    template = _select_template(template_id, custom_template)
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd cim-generator && python -m pytest tests/test_llm_template_selection.py -v`
Expected: PASS (3 passed)

- [x] **Step 5: Run the full existing test suite to confirm nothing else broke**

Run: `cd cim-generator && python -m pytest -v`
Expected: PASS (all existing tests still pass — `generate_cim_html`'s only behavioral change is additive)

---

### Task 5: Thread `custom_template` through the request → state → formatter pipeline

**Files:**
- Modify: `server.py:67-76` (`CIMRequest`), `server.py:190-212` (`_build_initial_state`)
- Modify: `state.py:20-27` (`CIMState`)
- Modify: `nodes/formatter.py:80-109` (`formatter_node`)
- Test: `tests/test_formatter.py` (append), `tests/test_server.py` (append)

**Interfaces:**
- Consumes: `core.llm.generate_cim_html(..., custom_template=...)` (Task 4).
- Produces: `CIMRequest.custom_template: dict | None`, `CIMState["custom_template"]` — both consumed end-to-end by the existing `/generate-cim/*` endpoints with zero other changes, since they all route through `_build_initial_state` and `formatter_node`.

- [x] **Step 1: Write the failing formatter test**

Append to `tests/test_formatter.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch

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
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd cim-generator && python -m pytest tests/test_formatter.py -v -k CustomTemplate`
Expected: FAIL — `generate_cim_html` mock called without a `custom_template` kwarg (`KeyError: 'custom_template'`)

- [x] **Step 3: Implement — `state.py`**

In `state.py`, in the `CIMState` class, add one field after `template_id: str` (line 27):

```python
    custom_template: dict | None
```

- [x] **Step 4: Implement — `nodes/formatter.py`**

In `formatter_node` (line 80-109), add after the `template_id` line (line 87):

```python
    custom_template: dict | None = state.get("custom_template")
```

And add `custom_template=custom_template,` to the `generate_cim_html(...)` call (line 104-109), so it reads:

```python
    html = await generate_cim_html(
        all_findings, listing_xml, listing_name, asking_price, all_images,
        logo_b64=logo_b64, logo_mime=logo_mime,
        brand_primary=brand_primary, brand_accent=brand_accent,
        template_id=template_id,
        custom_template=custom_template,
    )
```

- [x] **Step 5: Run tests to verify they pass**

Run: `cd cim-generator && python -m pytest tests/test_formatter.py -v -k CustomTemplate`
Expected: PASS (2 passed)

- [x] **Step 6: Write the failing server test**

Append to `tests/test_server.py`:

```python
def test_build_initial_state_carries_custom_template(client):
    from server import _build_initial_state, CIMRequest
    req = CIMRequest(
        listing_data=MINIMAL_LISTING,
        custom_template={"id": "custom-upload", "name": "My Upload"},
    )
    state = _build_initial_state(req)
    assert state["custom_template"] == {"id": "custom-upload", "name": "My Upload"}


def test_build_initial_state_defaults_custom_template_to_none(client):
    from server import _build_initial_state, CIMRequest
    req = CIMRequest(listing_data=MINIMAL_LISTING)
    state = _build_initial_state(req)
    assert state["custom_template"] is None
```

- [x] **Step 7: Run tests to verify they fail**

Run: `cd cim-generator && python -m pytest tests/test_server.py -v -k custom_template`
Expected: FAIL — `CIMRequest` has no field `custom_template` (pydantic raises on the unexpected kwarg, or `state["custom_template"]` raises `KeyError`)

- [x] **Step 8: Implement — `server.py`**

In `CIMRequest` (line 67-76), add after `template_id: str = "classic"`:

```python
    custom_template: dict[str, Any] | None = None  # Extracted from an uploaded PDF, see core/pdf_style_extractor.py
```

In `_build_initial_state` (line 190-212), add after `"template_id": req.template_id or "classic",`:

```python
        "custom_template": req.custom_template,
```

- [x] **Step 9: Run tests to verify they pass**

Run: `cd cim-generator && python -m pytest tests/test_server.py -v -k custom_template`
Expected: PASS (2 passed)

- [x] **Step 10: Run the full existing test suite to confirm nothing else broke**

Run: `cd cim-generator && python -m pytest -v`
Expected: PASS (all tests, including the pre-existing ones)

---

### Task 6: `POST /template/upload` endpoint

**Files:**
- Modify: `server.py` (imports at top, new endpoint near the other `/generate-cim/*` endpoints)
- Test: `tests/test_server.py` (append)

**Interfaces:**
- Consumes: `core.pdf_style_extractor.extract_style_profile`, `core.pdf_style_extractor.NoExtractableTextError` (Task 3); `tests/pdf_helpers.py` (Task 3).
- Produces: `POST /template/upload` — accepts `multipart/form-data` with a `file` field, returns `{"template": {...}, "warnings": [...]}` on success or a 4xx with `{"detail": "..."}` on failure. Consumed by Task 7's CRM UI.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_server.py`:

```python
def test_template_upload_returns_template_and_warnings(client):
    from tests.pdf_helpers import make_text_pdf
    pdf_bytes = make_text_pdf()
    response = client.post(
        "/template/upload",
        files={"file": ("template.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["template"]["id"] == "custom-upload"
    assert isinstance(body["warnings"], list)


def test_template_upload_rejects_non_pdf(client):
    response = client.post(
        "/template/upload",
        files={"file": ("notes.txt", b"just some text", "text/plain")},
    )
    assert response.status_code == 400


def test_template_upload_rejects_no_text_layer_pdf(client):
    from tests.pdf_helpers import make_no_text_pdf
    pdf_bytes = make_no_text_pdf()
    response = client.post(
        "/template/upload",
        files={"file": ("scanned.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 422
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd cim-generator && python -m pytest tests/test_server.py -v -k template_upload`
Expected: FAIL with 404 (no such route yet)

- [x] **Step 3: Implement**

In `server.py`, change the import line:

```python
from fastapi import FastAPI, HTTPException
```

to:

```python
from fastapi import FastAPI, File, HTTPException, UploadFile
```

Add near the other core imports (after `from core.templates import TEMPLATES`):

```python
from core.pdf_style_extractor import NoExtractableTextError, extract_style_profile
```

Add the new endpoint next to the other `/generate-cim/*` endpoints (after `generate_cim_json`, before `template_preview`):

```python
@app.post("/template/upload")
async def template_upload(file: UploadFile = File(...)):
    """Extract a CIM template style (colors/fonts/layout) from an uploaded PDF. No LLM."""
    is_pdf = (file.content_type == "application/pdf") or (file.filename or "").lower().endswith(".pdf")
    if not is_pdf:
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    pdf_bytes = await file.read()
    try:
        template, warnings = extract_style_profile(pdf_bytes)
    except NoExtractableTextError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return JSONResponse(content={"template": template, "warnings": warnings})
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd cim-generator && python -m pytest tests/test_server.py -v -k template_upload`
Expected: PASS (3 passed)

- [x] **Step 5: Run the full existing test suite to confirm nothing else broke**

Run: `cd cim-generator && python -m pytest -v`
Expected: PASS (all tests)

---

### Task 7: CRM UI — real upload flow in `view.php`

**Files:**
- Modify: `/opt/homebrew/var/www/new_ui/custom/protected/modules/clistings/views/clistings/view.php`
  - The `#cim-upload-coming-soon-modal` markup (added in the prior session) is replaced with a real file-input modal.
  - The `window.selectVerticaUploadTemplate` / `showCimUploadComingSoon` / `hideCimUploadComingSoon` JS functions are replaced with real upload logic.

**Interfaces:**
- Consumes: `POST /template/upload` (Task 6) on the `cim-generator` service.
- Produces: on successful upload, calls into the same "pick listing/files → generate" flow already used by `selectVerticaCustomTemplate`, but passing `custom_template` (the returned template dict) instead of a `template_id` in the generate request body.

- [x] **Step 1: Replace the coming-soon modal markup**

Find this block (added in the prior session, id `cim-upload-coming-soon-modal`):

```php
<!-- Upload Custom Template Coming Soon Modal -->
<div id="cim-upload-coming-soon-modal" class="cim-coming-soon-modal" aria-hidden="true" role="dialog" aria-modal="true" aria-labelledby="cim-upload-coming-soon-heading">
    <div class="cim-coming-soon-content">
        <div class="cim-coming-soon-body">
            <div class="cim-coming-soon-icon">&#128228;</div>
            <h3 id="cim-upload-coming-soon-heading"><?php echo Yii::t('app', 'Upload Your Own Template'); ?></h3>
            <p><?php echo Yii::t('app', "Coming soon: upload your own branded template and we'll generate your CIM in that exact design."); ?></p>
            <button type="button" onclick="hideCimUploadComingSoon(); return false;"><?php echo Yii::t('app', 'Got it'); ?></button>
        </div>
    </div>
</div>
```

Replace it with a real file-picker modal (reuses the same `cim-coming-soon-modal` CSS shell, swaps the content for a form):

```php
<!-- Upload Custom Template Modal -->
<div id="cim-upload-template-modal" class="cim-coming-soon-modal" aria-hidden="true" role="dialog" aria-modal="true" aria-labelledby="cim-upload-template-heading">
    <div class="cim-coming-soon-content">
        <div class="cim-coming-soon-body">
            <div class="cim-coming-soon-icon">&#128228;</div>
            <h3 id="cim-upload-template-heading"><?php echo Yii::t('app', 'Upload Your Own Template'); ?></h3>
            <p id="cim-upload-template-message"><?php echo Yii::t('app', 'Choose a PDF and we\'ll generate your CIM matching its exact colors, fonts, and layout.'); ?></p>
            <input type="file" id="cim-upload-template-file" accept="application/pdf" style="margin:12px 0;" />
            <div>
                <button type="button" onclick="hideCimUploadTemplateModal(); return false;"><?php echo Yii::t('app', 'Cancel'); ?></button>
                <button type="button" onclick="submitCimUploadTemplate(); return false;"><?php echo Yii::t('app', 'Upload'); ?></button>
            </div>
        </div>
    </div>
</div>
```

- [x] **Step 2: Replace the JS functions**

Find this block (added in the prior session):

```js
window.selectVerticaUploadTemplate = function() {
	hideCimVerticaModeModal();
	showCimUploadComingSoon();
};

window.showCimUploadComingSoon = function() {
	var modal = document.getElementById('cim-upload-coming-soon-modal');
	if (!modal) return;
	modal.classList.add('visible');
	modal.setAttribute('aria-hidden', 'false');
	document.body.classList.add('cim-modal-open');
};

window.hideCimUploadComingSoon = function() {
	var modal = document.getElementById('cim-upload-coming-soon-modal');
	if (!modal) return;
	modal.classList.remove('visible');
	modal.setAttribute('aria-hidden', 'true');
	document.body.classList.remove('cim-modal-open');
};
```

Replace it with:

```js
window.selectVerticaUploadTemplate = function() {
	hideCimVerticaModeModal();
	showCimUploadTemplateModal();
};

window.showCimUploadTemplateModal = function() {
	var modal = document.getElementById('cim-upload-template-modal');
	if (!modal) return;
	modal.classList.add('visible');
	modal.setAttribute('aria-hidden', 'false');
	document.body.classList.add('cim-modal-open');
};

window.hideCimUploadTemplateModal = function() {
	var modal = document.getElementById('cim-upload-template-modal');
	if (!modal) return;
	modal.classList.remove('visible');
	modal.setAttribute('aria-hidden', 'true');
	document.body.classList.remove('cim-modal-open');
};

window.submitCimUploadTemplate = function() {
	var input = document.getElementById('cim-upload-template-file');
	var messageEl = document.getElementById('cim-upload-template-message');
	if (!input || !input.files || !input.files[0]) {
		if (messageEl) messageEl.textContent = 'Please choose a PDF file first.';
		return;
	}
	var formData = new FormData();
	formData.append('file', input.files[0]);
	if (messageEl) messageEl.textContent = 'Analyzing your PDF…';

	fetch(window.VERTICA_CIM_BASE + '/template/upload', {
		method: 'POST',
		body: formData,
	})
		.then(function(res) {
			return res.json().then(function(data) { return { ok: res.ok, data: data }; });
		})
		.then(function(result) {
			if (!result.ok) {
				if (messageEl) messageEl.textContent = result.data.detail || 'Could not read that PDF.';
				return;
			}
			if (result.data.warnings && result.data.warnings.length) {
				console.warn('[VerticaCIM] Upload: no heading match in PDF for sections:', result.data.warnings);
			}
			hideCimUploadTemplateModal();
			selectCimCustomUploadTemplate(result.data.template);
		})
		.catch(function() {
			if (messageEl) messageEl.textContent = 'Upload failed — please try again.';
		});
};

window.selectCimCustomUploadTemplate = function(customTemplate) {
	// Reuses the exact same listing-fetch + SSE-generation flow as the 5 built-in
	// templates (see selectCimTemplate/startVerticaCimSse) — only the style source differs.
	window._cimCustomTemplate = customTemplate;
	selectCimTemplate('classic');
};
```

**Note (resolved during implementation):** `window.VERTICA_CIM_BASE` is the actual base-URL global already defined in this file (line 65) — not the placeholder `CIM_GENERATOR_BASE_URL` name originally guessed above. The actual call site where `template_id` is built into the generate request is `startVerticaCimSse()` (~line 1685), not a separate function — it was extended directly:

```js
	var payload = Object.assign({}, listingData, {
		callback_url: callbackUrl,
		listing_id: listingData.listing_id || (window.cimDisclaimerState && window.cimDisclaimerState.pendingListingId) || 0,
		template_id: window._cimSelectedTemplateId || 'classic',
		custom_template: window._cimCustomTemplate || null,
	});
	window._cimCustomTemplate = null;   // one-shot — consumed into this generation request
```

`window._cimCustomTemplate` is also reset to `null` at the top of `selectVerticaQuickCreate` and `selectVerticaCustomTemplate` so a stale upload never leaks into an unrelated generation.

- [x] **Step 3: Verify PHP syntax**

Run: `php -l /opt/homebrew/var/www/new_ui/custom/protected/modules/clistings/views/clistings/view.php`
Expected: `No syntax errors detected` — confirmed.

- [x] **Step 4: Wire the upload result into the existing generate call**

Done as part of Step 2/3 above — `selectCimCustomUploadTemplate()` sets `window._cimCustomTemplate` and calls the existing `selectCimTemplate('classic')`, and `startVerticaCimSse()`'s payload now includes `custom_template`.

- [x] **Step 5: Verify PHP syntax again**

Run: `php -l /opt/homebrew/var/www/new_ui/custom/protected/modules/clistings/views/clistings/view.php`
Expected: `No syntax errors detected` — confirmed.

- [ ] **Step 6: Manual verification (blocked by pre-existing environment issue)**

Live click-through in the CRM (upload a PDF, confirm generation uses it) is blocked by the pre-existing `CDbConnection failed to open the DB connection` error flagged earlier in this project — unrelated to this change. Once the CRM's DB connection is restored, manually verify: upload a PDF → see it analyzed → generate a CIM → confirm the output's colors/fonts match the uploaded PDF.

---

## Self-Review

**Spec coverage:**
- Deterministic PDF extraction (no LLM) — Tasks 2, 3. ✓
- Per-section coloring/structure, not just one global palette — Task 3's `_band_color` (accent) plus separate `primary`/`mid` from heading/body text colors. ✓
- Section-taxonomy matching, unmatched sections flagged not dropped — Task 1 (`match_sections`) + Task 3 (`warnings` list). ✓
- Content generation unchanged (Claude still writes prose) — Task 4 only adds a template-selection branch; the Claude call itself is untouched. ✓
- No OCR / reject scanned PDFs — Task 3 (`NoExtractableTextError`). ✓
- No persistent template library — nothing in this plan writes an uploaded template to storage; it only flows through one request. ✓
- CRM UI upload entry point — Task 7. ✓

**Placeholder scan:** Resolved during implementation — Task 7's original Step 4 placeholder (`startCimGenerationWithCustomTemplate`/`CIM_GENERATOR_BASE_URL`, guessed names) was replaced with the real call site (`startVerticaCimSse()`, `window.VERTICA_CIM_BASE`) found by grepping the actual file. No placeholders remain anywhere in the executed plan.

**Type consistency:** `extract_style_profile` returns `tuple[dict, list[str]]` consistently in its docstring (Task 3), its test assertions (Task 3), and its consumer (Task 6's endpoint, which unpacks `template, warnings = extract_style_profile(pdf_bytes)`). `_select_template`'s signature (Task 4) matches how Task 5's `formatter_node` and Task 6 eventually supply `custom_template` — always `dict | None`.

**Scope check:** Single cohesive feature (PDF upload → matching CIM), no independent subsystems requiring separate plans. DOC/DOCX/HTML are out of scope per the spec and not referenced by any task here.

## Implementation Results

All tasks (1–7, except Task 7 Step 6's manual click-through, blocked by a pre-existing CRM DB-connection issue) implemented and verified:
- `core/section_matcher.py`, `core/pdf_style_extractor.py`, `core/llm.py` (`_select_template`), `state.py`, `nodes/formatter.py`, `server.py` (`CIMRequest.custom_template`, `_build_initial_state`, `POST /template/upload`), `requirements.txt` (`PyMuPDF`, `python-multipart`), and `view.php` (real upload modal + `selectCimCustomUploadTemplate` wiring).
- New/changed tests: `tests/test_section_matcher.py`, `tests/test_pdf_style_extractor.py`, `tests/test_llm_template_selection.py`, `tests/test_formatter_custom_template.py`, `tests/pdf_helpers.py`, additions to `tests/test_server.py`.
- Full suite (`python -m pytest`, excluding the pre-existing-broken `tests/test_formatter.py` — see note below): **83 passed, 0 failed** (486s — dominated by pre-existing slow SSE-stream tests, unrelated to this feature).
- **Pre-existing, unrelated bug found (not fixed, out of scope):** `tests/test_formatter.py` imports `_merge_dicts`/`_merge_value` from `nodes/aggregator.py`, but that module only defines `aggregator_node` — those two functions don't exist there. This predates this work; the new formatter-forwarding tests were placed in a separate file (`tests/test_formatter_custom_template.py`) to avoid coupling to it.

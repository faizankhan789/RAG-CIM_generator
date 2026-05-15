"""Shared Anthropic Claude client + CIM extraction / HTML generation helpers."""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

import anthropic

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8192
MAX_HTML_TOKENS = 32000

_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


# ── Per-file extraction prompt ────────────────────────────────────────────────

_EXTRACTION_PROMPT = """\
You are a senior financial analyst reviewing documents for a Confidential Information Memorandum (CIM).

You may receive two types of input:
1. <ListingContext> XML — structured CRM data (asking price, financials, location, broker info, etc.)
2. Document content — PDF, spreadsheet, presentation, or image from the data room

PRIORITY RULES:
- <ListingContext> is ground truth for all factual fields it covers. Use it as-is.
- For information NOT in <ListingContext>: extract from the document.
- Where both overlap: combine, with <ListingContext> taking precedence for figures.

CRITICAL — numbers:
- Copy ALL financial figures, percentages, dates, and quantities EXACTLY as they appear.
- Never round, abbreviate, convert, or reformat any number.
- Preserve currency symbols, units (USD, %, x, bps), and date formats exactly.
- Never invent or infer numbers not explicitly stated.

Extract ALL relevant CIM information from the content. Use clear markdown headings for each topic area you find.
Only include sections where you found actual data — do not output empty sections.
Cover anything relevant: financials, operations, management, products, market, legal, risks, growth, etc.
Also note any industry-specific signals (business type, sector, industry terminology, KPIs used) — these help generate an industry-appropriate CIM.

Return your findings as structured markdown text. No JSON. No preamble."""


async def extract_from_content(
    content_blocks: list[dict],
    source_url: str,
    listing_xml: str = "",
) -> str:
    """Call Claude with file content. Returns free-form markdown findings text."""
    client = get_client()
    user_content: list[dict] = []
    if listing_xml:
        user_content.append({
            "type": "text",
            "text": (
                "The following is structured CRM data about this listing — treat it as ground truth:\n\n"
                + listing_xml
            ),
        })
    user_content.extend(content_blocks)
    user_content.append({"type": "text", "text": _EXTRACTION_PROMPT})
    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": user_content}],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        log.error("Extraction failed (%s): %s", source_url, exc)
        return ""


# ── Final HTML generation ─────────────────────────────────────────────────────

_HTML_PROMPT = """\
You are a world-class investment banking designer creating a stunning, print-ready Confidential Information Memorandum (CIM).
This document must look like it came from Goldman Sachs or Lazard — polished, premium, visually compelling.

═══════════════════════════════════════════════
STEP 1 — DETECT INDUSTRY
═══════════════════════════════════════════════
Identify the business type from the content. Examples:
Hotel/Hospitality, Restaurant/Food & Beverage, Travel & Tourism, Healthcare, Technology/SaaS,
Retail, Manufacturing, Real Estate, Education, Logistics, Professional Services, E-commerce.

═══════════════════════════════════════════════
STEP 2 — CHOOSE DESIGN SYSTEM
═══════════════════════════════════════════════
Pick the palette and personality that matches the industry:

- Hotel/Hospitality     → primary:#1a0a0e  accent:#b8902a  light:#fdf6ec  mid:#6b1f2a   — opulent, serif-inspired
- Restaurant/Food       → primary:#1c0f0a  accent:#c9622a  light:#fef9f5  mid:#7a3520   — warm, bold, appetising
- Travel/Tourism        → primary:#021e35  accent:#e6a817  light:#f0f8ff  mid:#0d4f72   — adventurous, bright
- Healthcare            → primary:#041f1f  accent:#1fa37a  light:#f5fffd  mid:#1a6b6b   — clinical, trustworthy
- Technology/SaaS       → primary:#07080f  accent:#6366f1  light:#f5f6ff  mid:#0f172a   — modern, sharp
- Retail                → primary:#180d06  accent:#e05c2a  light:#fff8f5  mid:#2d3748   — energetic, commercial
- Real Estate           → primary:#0a1a0f  accent:#c9973a  light:#f8fbf4  mid:#1a4731   — prestigious, grounded
- Manufacturing         → primary:#0d1117  accent:#e67e22  light:#fafafa  mid:#2c3e50   — industrial, confident
- Education             → primary:#0a1229  accent:#f59e0b  light:#fffdf0  mid:#1e3a8a   — academic, approachable
- Professional Services → primary:#0f1117  accent:#64748b  light:#f8fafc  mid:#1f2937   — understated authority
- If no match: pick an appropriate premium dark primary + gold/warm accent

Industry KPIs — use ONLY metrics actually present in the data:
- Hotel: RevPAR, ADR, Occupancy %, Total Keys, F&B Revenue, GOP Margin, Star Rating
- Restaurant: Covers/Day, Avg Check, COGS%, Labour%, Seat Turns, Cuisine, Seating Capacity
- Travel: Booking Volume, Destinations, Repeat Rate, Package Types, Peak Season
- SaaS/Tech: ARR/MRR, Churn, CAC, LTV, NPS, Gross Margin, DAU/MAU
- Healthcare: Patient Volume, Procedures/Day, Payer Mix, Certifications
- Retail: Same-Store Sales, Inventory Turns, SKU Count, Basket Size
- Real Estate: Cap Rate, NOI, Occupancy %, Lease Terms, Price/SqFt

═══════════════════════════════════════════════
STEP 3 — BUILD THE DOCUMENT
═══════════════════════════════════════════════

CRITICAL DATA RULES:
- Copy ALL financial figures, percentages, dates EXACTLY — never round, abbreviate, or infer.
- Include ONLY sections where you have actual data — skip sections with no content.
- Never invent metrics, names, or figures not present in the source data.

━━━━━━━━━━━━━━━━━━━━━━━━
PAGE 1 — COVER
━━━━━━━━━━━━━━━━━━━━━━━━
Full-viewport cover page (min-height:100vh). Structure:
- Background: diagonal or radial gradient from primary → mid (dark, rich)
- Decorative geometric shapes: large semi-transparent circles or diagonal bands in accent color, low opacity (0.08–0.15), absolutely positioned — creates depth without clutter
- Top bar: "CONFIDENTIAL INFORMATION MEMORANDUM" in small-caps tracking-widest, accent color, subtle top border in accent
- CENTER BLOCK (vertically centered):
    • Industry badge pill (e.g. "RESTAURANT & FOOD SERVICE") — accent background, white text, rounded-full, uppercase, letter-spacing
    • Business name: massive (clamp(3rem,8vw,6rem)), white, bold, line-height 1.1, max 2 lines
    • Tasteful thin horizontal rule in accent color below name
    • Tagline or location if available — light/60 color, italic, 1.2rem
    • Asking price block: large accent-colored chip — "Asking Price" label above, price value bold 2.5rem white
- BOTTOM BAR: dark translucent band across full width:
    • Left: "Prepared exclusively for prospective acquirers" — muted italic
    • Center: current date
    • Right: "STRICTLY PRIVATE & CONFIDENTIAL"
- If images are available: place the best property/exterior/product photo as <!-- IMG:1 --> BEHIND the center block as a full-bleed background image with a dark overlay (overlay div with background rgba(0,0,0,0.55)), so the image is visible but text is clear. Use object-fit:cover, position:absolute, width:100%, height:100%, z-index:0. All text content at z-index:1.

━━━━━━━━━━━━━━━━━━━━━━━━
PAGE 2 — TABLE OF CONTENTS
━━━━━━━━━━━━━━━━━━━━━━━━
Clean, typographically elegant. Full-page feel (min-height:80vh, light background).
- Top: "TABLE OF CONTENTS" heading in primary color, large, bold
- Thin accent-colored top border
- Each section: flex row — Roman numeral (accent color, bold, monospace), section title (primary, medium weight), dotted leader line, page anchor arrow →
- Hover state: background tint, cursor pointer
- Subtle section groupings if many sections

━━━━━━━━━━━━━━━━━━━━━━━━
KEY METRICS STRIP
━━━━━━━━━━━━━━━━━━━━━━━━
Immediately after TOC — a full-width horizontal strip of stat cards (3–6 cards):
- Background: primary color
- Each card: centered, white label (small, uppercase, muted), large white value (bold, 2rem+), optional unit label
- Cards separated by thin vertical lines
- Only use metrics actually present in the data

━━━━━━━━━━━━━━━━━━━━━━━━
CONTENT SECTIONS
━━━━━━━━━━━━━━━━━━━━━━━━
Each section (id="section-N") follows this pattern:

SECTION HEADER:
- Full-width band: gradient from primary to mid, padding 2.5rem 3rem
- Roman numeral (accent, 0.85rem, letter-spacing) above title
- Section title: white, 2rem, bold
- Optional 1-line description: white/70, italic, 0.95rem
- Decorative right-side accent bar or geometric element

SECTION BODY (white or light background, generous padding):
Use the most appropriate layout for the content type:

• EXECUTIVE SUMMARY / INVESTMENT HIGHLIGHTS:
  - Opening paragraph: large pull-quote style (1.15rem, line-height 1.8, border-left 4px accent)
  - Bullet highlights as icon-less styled list items with accent left-dot

• FINANCIAL TABLES:
  - Full-width table, thead: primary bg, white text
  - Tbody: alternating white / light-bg rows
  - Numbers: right-aligned, monospace font
  - Total/summary rows: bold, accent-tinted background
  - Currency labels: muted, smaller

• IMAGE PLACEMENT (<!-- IMG:N -->):
  - Hero/exterior/product shots: full-width poster format (max-height:480px, object-fit:cover, border-radius:12px, box-shadow:0 8px 32px rgba(0,0,0,0.18))
  - Interior/detail shots: 2-column grid if 2+ images available (gap:1.5rem)
  - Team/headshot photos: circular crop (border-radius:50%), 120px diameter, centered
  - Each image: figcaption below in muted italic
  - Place images where they are CONTEXTUALLY relevant — property photo near Property Details, food shots near Menu/Concept section, etc.
  - DO NOT cluster all images together — spread them throughout the document
  - Use <!-- IMG:N --> markers generously if images are available — they make the CIM dramatically more compelling

• TWO-COLUMN LAYOUT (for details/overview sections):
  - Left 60% narrative text, right 40% highlight box (accent-tinted bg, border-radius:12px, padding:1.5rem)
  - Highlight box contains key facts, bullet points, or a single standout metric

• SWOT (if data available):
  - 2×2 grid, each quadrant full card with colored top border:
    Strengths → #16a34a (green), Weaknesses → #dc2626 (red)
    Opportunities → #2563eb (blue), Threats → #d97706 (amber)
  - Quadrant title: colored, bold, uppercase, small
  - Items: clean bulleted list

• MANAGEMENT TEAM (if data available):
  - Card row, each card: white bg, subtle shadow, border-radius:12px, padding:1.5rem
  - Name: bold 1.1rem, Title: accent color 0.9rem, Bio: muted 0.85rem

• GROWTH & STRATEGY / INVESTMENT THESIS:
  - Numbered steps or milestone timeline with accent-colored step indicators
  - Each step: number circle (accent bg, white text), title bold, description muted

━━━━━━━━━━━━━━━━━━━━━━━━
LAST PAGE — DISCLAIMER
━━━━━━━━━━━━━━━━━━━━━━━━
Dark footer page (primary bg):
- "IMPORTANT NOTICE & DISCLAIMER" in accent, centered
- Boilerplate confidentiality text in white/60, small, centered
- Centered accent horizontal rule

═══════════════════════════════════════════════
TECHNICAL REQUIREMENTS
═══════════════════════════════════════════════
- Fully self-contained HTML — ALL CSS inside one <style> tag. Zero external resources, CDN links, or web fonts.
- Font stack: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif
- Max content width: 1000px, centered with auto margins
- The cover page and TOC are full-viewport — subsequent sections have generous padding (3rem+)
- Smooth scroll: html { scroll-behavior: smooth }
- Financial numbers: font-variant-numeric: tabular-nums
- Print media: @media print { .no-print { display:none } section { page-break-before: always } }
- The document should feel HEAVY and SUBSTANTIAL — not lightweight. Use ample whitespace, large typography, rich backgrounds.

Return ONLY the complete HTML document starting with <!DOCTYPE html>. No explanation, no markdown fences."""


_MIN_DIM = 32


def _is_valid_image(b64: str, mime: str, label: str) -> bool:
    """Return False if image is corrupt, too small, or unsupported — skip it rather than 400."""
    try:
        from PIL import Image
        raw = base64.standard_b64decode(b64)
        img = Image.open(io.BytesIO(raw))
        img.verify()
        img = Image.open(io.BytesIO(raw))
        w, h = img.size
        if w < _MIN_DIM or h < _MIN_DIM:
            log.error("HTML gen: dropping image %r — too small (%dx%d)", label, w, h)
            return False
        return True
    except Exception as exc:
        log.error("HTML gen: dropping image %r — %s", label, exc)
        return False


def _build_image_tag(img: dict, index: int) -> str:
    """Build an HTML figure element with embedded base64 image."""
    label = img.get("label", f"Image {index}")
    mime = img.get("mime", "image/jpeg")
    b64 = img.get("b64", "")
    return (
        f'<figure class="cim-image">'
        f'<img src="data:{mime};base64,{b64}" alt="{label}" '
        f'style="max-width:100%;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,0.15);display:block;margin:0 auto;"/>'
        f'<figcaption style="text-align:center;font-size:0.85em;color:#666;margin-top:8px;">{label}</figcaption>'
        f'</figure>'
    )


async def generate_cim_html(
    all_findings: list[str],
    listing_xml: str,
    listing_name: str,
    asking_price: str,
    all_images: list[dict] | None = None,
) -> str:
    """Send all per-file findings + listing context + images to Claude → returns full HTML CIM."""
    client = get_client()
    all_images = all_images or []

    user_content: list[dict] = []

    if listing_xml:
        user_content.append({
            "type": "text",
            "text": "## Structured CRM Data (ground truth)\n\n" + listing_xml,
        })

    if listing_name:
        user_content.append({"type": "text", "text": f"Business Name: {listing_name}"})

    if asking_price:
        user_content.append({"type": "text", "text": f"Asking Price: {asking_price}"})

    for i, finding in enumerate(all_findings, start=1):
        if finding.strip():
            user_content.append({
                "type": "text",
                "text": f"## Document {i} Findings\n\n{finding}",
            })

    # Filter out invalid/corrupt images before sending to Claude
    valid_images: list[tuple[int, dict]] = []
    for orig_i, img in enumerate(all_images, start=1):
        b64 = img.get("b64", "")
        mime = img.get("mime", "image/jpeg")
        label = img.get("label", f"Image {orig_i}")
        if b64 and _is_valid_image(b64, mime, label):
            valid_images.append((orig_i, img))
        else:
            pass  # already logged by _is_valid_image

    if len(valid_images) < len(all_images):
        log.error("HTML gen: %d/%d images dropped (invalid/corrupt)", len(all_images) - len(valid_images), len(all_images))

    # Send each image as a vision block so Claude can see it and judge relevance
    if valid_images:
        user_content.append({
            "type": "text",
            "text": (
                f"## Images ({len(valid_images)} available)\n"
                "Below are images from the data room / image gallery. "
                "For each image, decide if it adds visual value to the CIM "
                "(property photos, product shots, facility images, team photos, charts, etc.). "
                f"To embed image N, place the exact marker <!-- IMG:N --> in your HTML where you want it. "
                "The system will replace the marker with the actual image. "
                "Use good placement — inside the relevant section, with surrounding context. "
                "Skip images that are not visually meaningful (icons, thumbnails, decorative images)."
            ),
        })
        for seq_i, (orig_i, img) in enumerate(valid_images, start=1):
            mime = img.get("mime", "image/jpeg")
            b64 = img.get("b64", "")
            label = img.get("label", f"Image {seq_i}")
            user_content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": b64},
            })
            user_content.append({
                "type": "text",
                "text": f"(Above image is Image {seq_i}: \"{label}\". Use marker <!-- IMG:{seq_i} --> to embed it.)",
            })

    user_content.append({"type": "text", "text": _HTML_PROMPT})

    try:
        # Use streaming — HTML generation can exceed 10 minutes with many images
        async with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_HTML_TOKENS,
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            html = await stream.get_final_text()

        html = html.strip()

        # Strip accidental markdown fencing
        if html.startswith("```"):
            lines = html.split("\n")
            html = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        # Replace <!-- IMG:N --> markers with actual base64 img tags (sequential over valid images)
        for seq_i, (_orig_i, img) in enumerate(valid_images, start=1):
            marker = f"<!-- IMG:{seq_i} -->"
            if marker in html:
                html = html.replace(marker, _build_image_tag(img, seq_i))

        return html
    except Exception as exc:
        log.error("HTML generation failed: %s", exc)
        raise

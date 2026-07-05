"""Shared Anthropic Claude client + CIM extraction / HTML generation helpers."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
from typing import Any

import anthropic
from datetime import date

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8192
MAX_HTML_TOKENS = 32000

# Max concurrent LLM extraction calls (env-tunable, default 5)
_LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "5"))
_llm_sem: asyncio.Semaphore | None = None


def _get_llm_semaphore() -> asyncio.Semaphore:
    global _llm_sem
    if _llm_sem is None:
        _llm_sem = asyncio.Semaphore(_LLM_CONCURRENCY)
    return _llm_sem


_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


# ── Per-file extraction prompt ────────────────────────────────────────────────

_EXTRACTION_PROMPT = """\
You are a senior financial analyst extracting data from documents to build a Confidential Information Memorandum (CIM).

INPUT TYPES:
1. <ListingContext> XML — structured CRM data (asking price, financials, location, broker info, etc.)
2. Document content — PDF, spreadsheet, presentation, or image from the data room

PRIORITY RULES:
- <ListingContext> is ground truth for all factual fields it covers. Use it as-is.
- For information NOT in <ListingContext>: extract from the document.
- Where both overlap: combine, with <ListingContext> taking precedence for figures.

CRITICAL — numbers:
- Copy ALL financial figures, percentages, dates, and quantities EXACTLY as they appear in the source.
- Never round, abbreviate, convert, or reformat any number (e.g. £123,450 must stay £123,450 — not £123k, not £120,000).
- Preserve currency symbols, units (USD, £, €, %, x, bps), and date formats exactly.
- Never invent or infer numbers not explicitly stated in the source documents.
- If a number is not in the source, DO NOT include it — omit the subtopic entirely rather than estimate or approximate.
- Do NOT use industry benchmarks or typical ranges as if they are this business's actual figures.

INDUSTRY SIGNALS — always note:
- Business type, sector, cuisine type, service category, star rating, etc.
- Industry-specific KPIs present (RevPAR, ADR, COGS%, ARR, CAC, NOI, etc.)
- These signals are critical for generating an industry-appropriate CIM design.

EXTRACTION STRUCTURE:
Map everything you find to the relevant CIM section below.
Use these exact markdown headings — only include a heading if you found actual data for it.

## I. Executive Summary
- Brief overview of the business (what it does, where it operates)
- Key investment highlights and unique selling points
- Summary of financial performance (revenue, profit, margins — exact figures only)
- Asking price and key terms

## II. Company Overview
- History and background (founding year, milestones)
- Ownership structure
- Values
- Management team and key personnel (names, titles, tenure)
- Organizational chart (if org structure data present)
- Culture
- Vision and Mission
- Location and facilities (premises, lease/freehold, sq ft)
- Products and services
- Competitive advantages
- Market position and industry overview
- Social Responsibility & Sustainability
- SWOT analysis (strengths, weaknesses, opportunities, threats)

## III. Financial Information
- Historical financial statements (exact figures only)
- Adjusted EBITDA and other relevant metrics (exact figures only)
- Detailed breakdown of revenue streams
- Key performance indicators (KPIs) — industry-specific, exact values only
- Tax information
- Financial ratios and trends
- Projections and forecasts (only if explicitly stated in source)
- Cost structure analysis (COGS, labour, rent, overheads — exact figures)
- Capitalization table (if ownership/equity data present)
- Debt structure (loans, liabilities — exact figures)

## IV. Operations
- Manufacturing processes (adapt name to industry, e.g. Kitchen Operations)
- Supply chain and logistics
- Key suppliers and customers
- Quality certifications and standards
- Technology and equipment
- Technology infrastructure & security
- Research and development
- Intellectual property (patents, trademarks, recipes, software)
- Scalability and capacity for growth

## V. Marketing and Sales
- Target market and customer segmentation
- Marketing strategies and channels
- Marketing & sales budgets (if stated)
- Sales pipeline
- Customer churn rate (if stated)
- Sales processes and distribution channels
- Branding and advertising
- Customer acquisition costs (if stated)
- Customer relationship management

## VI. Legal and Regulatory
- Legal structure and compliance
- Permits and licenses
- Data privacy & security (GDPR, compliance)
- Insurance coverage
- Intellectual property strategy
- Contracts and agreements (leases, supplier, franchise)
- Environmental regulations
- International compliance (if applicable)
- Litigation and disputes

## VII. Human Resources
- Employee demographics and compensation (headcount, FT/PT, salary bands)
- Benefits and training programs
- Management succession plan
- Key employee retention strategies
- Employee turnover rate (if stated)
- Labor relations and unions

## VIII. Growth Opportunities
- Expansion plans and strategies
- New product development
- International expansion (if applicable)
- Strategic partnerships
- Market penetration and diversification
- Joint ventures & alliances
- Franchise opportunities (if applicable)
- Mergers and acquisitions

## IX. Risks
- Industry and market risks
- Competition
- Financial risks
- Reputation & brand risks
- Technology risks
- Political & economic risks
- Environmental, Social, and Governance (ESG) risks
- Operational risks
- Risk assessment matrix (if risk data is sufficient for a matrix)
- Legal and regulatory risks

## X. Appendix
- Detailed financial statements
- Market research data
- Appraisals and valuations
- Legal documents
- Customer testimonials
- Industry awards and recognition
- Customer journey map (if customer flow/experience data present)
- Competitive analysis matrix (if competitor data present)
- Value chain analysis (if value chain data present)
- Product lifecycle analysis (if product stage data present)
- Market segmentation map (if segmentation data present)

Return ONLY the populated markdown. No preamble, no commentary, no empty sections.
Map ALL data into sections I–X only. Do NOT create any section outside this list.
Omit any section or subtopic entirely if there is no real data for it."""


async def extract_from_content(
    content_blocks: list[dict],
    source_url: str,
    listing_xml: str = "",
) -> str:
    """Call Claude with file content. Returns free-form markdown findings text.

    Gated by _llm_sem so at most LLM_CONCURRENCY calls run simultaneously,
    preventing Claude API rate-limit errors when many files are processed.
    """
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
    async with _get_llm_semaphore():
        log.info("LLM extract: acquiring slot for %r", source_url)
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
⚑ BRAND COLORS: If a "Brand Colors" block appears in your input, use those hex values
  as primary and accent. Derive light (primary at ~5% opacity over white) and mid
  (midpoint blend of primary + accent). Do NOT use the industry defaults below in that case.

Fallback industry palettes (use ONLY if no brand colors provided):
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
STEP 2b — SECTION LAYOUT COMPONENTS
═══════════════════════════════════════════════
For each section body, pick component(s) from this library based on data volume and type.
Do NOT force a fixed layout — adapt to what the data actually supports.

COMPONENT LIBRARY:
▸ [stat-strip]      Horizontal KPI cards on dark background. Use when 3+ numeric metrics exist.
▸ [narrative-pull]  Large pull-quote paragraph with accent left-border. For text-rich, metric-light sections.
▸ [two-col-60-40]   Left 60% narrative + right 40% highlight box. Good for overview/intro sections.
▸ [two-col-50-50]   Equal columns. Use when two equally weighted topics exist side by side.
▸ [data-table]      Financial or comparison table. Use for any tabular or multi-period financial data.
▸ [card-grid-2]     2-column cards. Use for 2-4 equal items (team members, features, locations).
▸ [card-grid-3]     3-column cards. Use for 5+ equal items.
▸ [timeline]        Numbered vertical steps. Use for growth plans, milestones, roadmap, history.
▸ [swot-grid]       2×2 quadrant. Only for SWOT section.
▸ [image-hero]      Full-width image, max-height 480px. Use for best property/exterior/product shot.
▸ [image-mosaic]    2-3 column image gallery. Use when 3+ contextually relevant images exist.
▸ [bullet-list]     Styled accent-dot bullet points. Use for lists of 5+ items without card structure.

SELECTION RULES:
- Sparse data (1-3 points) → [narrative-pull] or [two-col-60-40]
- Rich financial data → [data-table] above or below a [stat-strip]
- Team section → [card-grid-2] (≤4 people) or [card-grid-3] (5+)
- Growth/strategy/roadmap → [timeline]
- Never use identical layout for two adjacent sections — vary for visual rhythm
- Combine freely: e.g. [stat-strip] + [two-col-60-40] + [data-table] all in one section

═══════════════════════════════════════════════
STEP 3 — BUILD THE DOCUMENT
═══════════════════════════════════════════════

CRITICAL DATA RULES:
- Copy ALL financial figures, percentages, dates EXACTLY as they appear in the source data — never round, abbreviate, or infer.
- Include ONLY sections where you have actual data — skip sections with no content.
- Never invent metrics, names, or figures not present in the source data.
- STRICT STRUCTURE: Generate ONLY sections I through X as defined above. Do NOT create any section, heading, or topic outside this list. No bonus sections, no summaries, no additional pages beyond Cover, TOC, sections I–X, and Disclaimer.
- IGNORE internal CRM metadata: do NOT include CRM IDs, usernames, system dates, listing status, campaign IDs, NDA flags, or any other internal admin fields in the document. These are system fields, not business content.
- EMPTY SUBTOPIC RULE: If a subtopic has no data, omit it entirely — do NOT show a heading with empty or placeholder content.

⛔ FINANCIAL NUMBER RULES — STRICTLY ENFORCED:
- Every number, figure, percentage, currency amount, ratio, and date in the output MUST come directly from the source data provided. NO exceptions.
- FORBIDDEN: rounding (e.g. source says £123,450 → do NOT write £123k or £120,000)
- FORBIDDEN: inferring (e.g. if only revenue is given → do NOT calculate or guess profit margin)
- FORBIDDEN: averaging or estimating (e.g. do NOT write "approximately £X" unless source says so)
- FORBIDDEN: industry benchmarks as if they are this business's numbers (e.g. do NOT write "typical margins of 15%" as if it applies here)
- FORBIDDEN: projections or forecasts unless explicitly stated in source documents
- If a financial figure is NOT in the source data, leave that subtopic out entirely — do not substitute, estimate, or approximate.
- When in doubt: OMIT rather than invent.

━━━━━━━━━━━━━━━━━━━━━━━━
PAGE 1 — COVER
━━━━━━━━━━━━━━━━━━━━━━━━
Full-page cover (min-height:1080px). Structure:
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
Clean, typographically elegant. Full-page feel (min-height:700px, light background).
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
CIM STRUCTURE — 10 SECTIONS
━━━━━━━━━━━━━━━━━━━━━━━━
Use this exact section and subtopic structure as the backbone of every CIM.
ONLY include subtopics where you have actual data — skip any subtopic with no content.
Adapt terminology to the detected industry (e.g. "Manufacturing processes" → "Kitchen Operations" for a restaurant).

I. Executive Summary
   • Brief overview of the business
   • Key investment highlights
   • Summary of financial performance
   • Asking price and key terms

II. Company Overview
   • History and background
   • Ownership structure
   • Values
   • Management team and key personnel
   • Organizational chart
   • Culture
   • Vision and Mission
   • Location and facilities
   • Products and services
   • Competitive advantages
   • Market position and industry overview
   • Social Responsibility & Sustainability
   • SWOT analysis (2×2 grid)

III. Financial Information
   • Historical financial statements
   • Adjusted EBITDA and other relevant metrics
   • Detailed breakdown of revenue streams
   • Key performance indicators (KPIs)
   • Tax information
   • Financial ratios and trends
   • Projections and forecasts
   • Cost structure analysis
   • Capitalization table
   • Debt structure

IV. Operations
   • Manufacturing processes (adapt name to industry, e.g. Kitchen Operations for restaurants)
   • Supply chain and logistics
   • Key suppliers and customers
   • Quality certifications and standards
   • Technology and equipment
   • Technology infrastructure & security
   • Research and development
   • Intellectual property
   • Scalability and capacity for growth

V. Marketing and Sales
   • Target market and customer segmentation
   • Marketing strategies and channels
   • Marketing & sales budgets
   • Sales pipeline
   • Customer churn rate
   • Sales processes and distribution channels
   • Branding and advertising
   • Customer acquisition costs
   • Customer relationship management

VI. Legal and Regulatory
   • Legal structure and compliance
   • Permits and licenses
   • Data privacy & security
   • Insurance coverage
   • Intellectual property strategy
   • Contracts and agreements
   • Environmental regulations
   • International compliance
   • Litigation and disputes

VII. Human Resources
   • Employee demographics and compensation
   • Benefits and training programs
   • Management succession plan
   • Key employee retention strategies
   • Employee turnover rate
   • Labor relations and unions

VIII. Growth Opportunities
   • Expansion plans and strategies
   • New product development
   • International expansion
   • Strategic partnerships
   • Market penetration and diversification
   • Joint ventures & alliances
   • Franchise opportunities
   • Mergers and acquisitions

IX. Risks
   • Industry and market risks
   • Competition
   • Financial risks
   • Reputation & brand risks
   • Technology risks
   • Political & economic risks
   • Environmental, Social, and Governance (ESG) risks
   • Operational risks
   • Risk assessment matrix
   • Legal and regulatory risks

X. Appendix
   • Detailed financial statements
   • Market research data
   • Appraisals and valuations
   • Legal documents
   • Customer testimonials
   • Industry awards and recognition
   • Customer journey map
   • Competitive analysis matrix
   • Value chain analysis
   • Product lifecycle analysis
   • Market segmentation map

━━━━━━━━━━━━━━━━━━━━━━━━
CONTENT SECTIONS — LAYOUT
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
- DO NOT add any sticky or fixed navigation bar, top bar, or header bar with section links — no nav element at all.
- Fully self-contained HTML — ALL CSS inside one <style> tag. Zero external resources, CDN links, or web fonts.
- Font stack: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif
- Max content width: 1000px, centered with auto margins
- The cover page and TOC use fixed-height pages — subsequent sections have generous padding (3rem+)
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
    logo_b64: str = "",
    logo_mime: str = "",
    brand_primary: str = "",
    brand_accent: str = "",
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

    if brand_primary and brand_accent:
        user_content.append({
            "type": "text",
            "text": (
                f"## Brand Colors\n"
                f"primary: {brand_primary}\n"
                f"accent: {brand_accent}\n"
                f"Use these exact hex values as primary and accent throughout the CIM "
                f"(cover, section headers, stat strips, accents). "
                f"Derive light = very pale tint of primary (~5% opacity over white). "
                f"Derive mid = blend of primary and accent at 50%. "
                f"Do NOT use the industry fallback palette from STEP 2."
            ),
        })
        log.info("LLM: brand colors injected — primary=%s accent=%s", brand_primary, brand_accent)

    logo_instruction = ""
    if logo_b64 and logo_mime:
        logo_instruction = (
            "## Company Logo\n"
            "A company logo is provided below as a base64 image. "
            "Place the exact marker <!-- LOGO --> where the logo should appear in the HTML "
            "(cover page top-left corner only). "
            "The system will replace <!-- LOGO --> with the actual <img> tag automatically.\n\n"
        )
        user_content.append({"type": "text", "text": logo_instruction})
        user_content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": logo_mime, "data": logo_b64},
        })

    user_content.append({
        "type": "text",
        "text": f"Today's date is {date.today().strftime('%B %d, %Y')}.\n\n" + _HTML_PROMPT,
    })

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

        # Inject logo at top-left of cover page regardless of where Claude placed <!-- LOGO -->
        if logo_b64 and logo_mime:
            logo_tag = (
                f'<div style="position:absolute;top:1.5rem;left:2rem;z-index:10;">'
                f'<img src="data:{logo_mime};base64,{logo_b64}" alt="Company Logo" '
                f'style="max-height:60px;max-width:180px;object-fit:contain;display:block;"/>'
                f'</div>'
            )
            # Remove any <!-- LOGO --> markers Claude may have placed
            html = html.replace("<!-- LOGO -->", "")
            # Inject right after <body> open tag so it sits over the cover page
            html = html.replace("<body>", f"<body>{logo_tag}", 1)

        # Replace <!-- IMG:N --> markers with actual base64 img tags (sequential over valid images)
        for seq_i, (_orig_i, img) in enumerate(valid_images, start=1):
            marker = f"<!-- IMG:{seq_i} -->"
            if marker in html:
                html = html.replace(marker, _build_image_tag(img, seq_i))

        return html
    except Exception as exc:
        log.error("HTML generation failed: %s", exc)
        raise

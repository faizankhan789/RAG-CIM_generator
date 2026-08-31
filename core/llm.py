"""Shared Anthropic Claude client + CIM extraction / HTML generation helpers."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import re
from typing import Any

import anthropic
from datetime import date

from core.templates import get_template

log = logging.getLogger(__name__)

MODEL = os.environ["CIM_MODEL"]  # set in .env or deployment env vars
MAX_TOKENS = 8192
MAX_HTML_TOKENS = 32000

# Global token accumulator — shared across all tasks in the process.
# reset_token_counters() called at pipeline start; get_token_counts() at end.
_tokens: dict[str, int] = {"input": 0, "output": 0}


def reset_token_counters() -> None:
    _tokens["input"] = 0
    _tokens["output"] = 0


def get_token_counts() -> tuple[int, int]:
    return _tokens["input"], _tokens["output"]


def _add_tokens(input_t: int, output_t: int) -> None:
    _tokens["input"]  += input_t
    _tokens["output"] += output_t

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
        log.debug("LLM extract: acquiring slot for %r", source_url)
        try:
            response = await client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": user_content}],
            )
            _add_tokens(response.usage.input_tokens, response.usage.output_tokens)
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
▸ [stat-strip]      Horizontal KPI cards on dark background, each topped with a matching ICON SYSTEM glyph. Use when 3+ numeric metrics exist.
▸ [narrative-pull]  Large pull-quote paragraph with accent left-border. For text-rich, metric-light sections.
▸ [two-col-60-40]   Left 60% narrative + right 40% highlight box. Good for overview/intro sections.
▸ [two-col-50-50]   Equal columns. Use when two equally weighted topics exist side by side.
▸ [data-table]      Financial or comparison table. Use for any tabular or multi-period financial data.
▸ [chart-bar]       Inline SVG bar chart for a trend across 2+ periods (e.g. revenue/EBITDA by year). See SVG CHART LIBRARY below.
▸ [chart-donut]     Inline SVG donut chart for a composition/mix breakdown (e.g. revenue by segment). See SVG CHART LIBRARY below.
▸ [card-grid-2]     2-column cards. Use for 2-4 equal items (team members, features, locations).
▸ [card-grid-3]     3-column cards. Use for 5+ equal items.
▸ [timeline]        Numbered vertical steps. Use for growth plans, milestones, roadmap, history.
▸ [swot-grid]       2×2 quadrant. Only for SWOT section.
▸ [image-hero]      Full-width image, max-height 480px. Use for best property/exterior/product shot.
▸ [image-mosaic]    2-3 column image gallery. Use when 3+ contextually relevant images exist.
▸ [bullet-list]     Styled accent-dot bullet points (or the matching ICON SYSTEM glyph in place of the dot for facilities/amenities/product lists). Use for lists of 5+ items without card structure.

SELECTION RULES:
- Sparse data (1-3 points) → [narrative-pull] or [two-col-60-40]
- Rich financial data → [data-table] above or below a [stat-strip]
- Trend across 2+ periods (revenue/EBITDA by year) → [chart-bar] beside its [data-table]; a composition/mix breakdown → [chart-donut] — the chart supplements the table, never replaces it
- Team section → [card-grid-2] (≤4 people) or [card-grid-3] (5+)
- Growth/strategy/roadmap → [timeline]
- Never use identical layout for two adjacent sections — vary for visual rhythm
- Combine freely: e.g. [stat-strip] + [two-col-60-40] + [data-table] all in one section

═══════════════════════════════════════════════
STEP 2c — ICON SYSTEM
═══════════════════════════════════════════════
A fixed set of inline monoline SVG icons. Copy the markup below VERBATIM (only the wrapping
<svg> tag's width/height/color may change, via CSS) — never redraw a path or invent a new
icon shape; hand-drawn path data renders broken or illegible at this scale.

Icon usage is DELIBERATE, not decorative. Use icons ONLY in these places:
1. Each [stat-strip] card — one icon above/beside the value (pick the closest semantic match
   below; skip the icon entirely rather than force a wrong one).
2. Facilities/amenities/product [bullet-list] items — icon replaces the accent-dot bullet.
   CRITICAL: a bullet-list is either ALL icon-led or ALL dot-led, never mixed on the same
   list. If any li in a [bullet-list] carries an icon <svg>, do NOT also write a
   `::before` content rule for that list's li — the icon IS the marker. Only add
   `.bullet-list li::before { content:'▸' }` for a list that has no icons at all. Writing
   both on the same li renders two competing markers side by side, out of alignment with
   each other — this is a common, easy-to-miss bug; check every [bullet-list] block for it.
3. Executive Summary / Investment Highlights list items — the `check` icon replaces the dot.
4. The SECTION FOOTER (see below) — the `lock` icon beside "STRICTLY PRIVATE & CONFIDENTIAL",
   only where the active template's style rules don't forbid extra ornamentation.
Do NOT scatter icons through body paragraphs, table cells, or section header bands — that
reads as a generic template, not a bank-grade CIM.

Shared attributes on every icon — these MUST be literal attributes on the <svg> tag itself,
every single time, with NO exceptions: viewBox="0 0 24 24" fill="none" stroke="currentColor"
stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" width="22" height="22".
A CSS class (e.g. `.metric-icon`, `.highlight-list li svg`) may RESIZE or RECOLOR the icon on
top of these, but never OMIT the width/height in the first place — a bare <svg> with no
explicit size defaults to the browser's native replaced-element size of 300×150px. This is a
common, easy-to-miss bug: if you write a CSS rule for an icon-bearing selector that only sets
layout properties (e.g. `flex-shrink:0`, `margin-top:...`) and forgets `width`/`height`, the
icon silently balloons to 300×150px, blowing that row/list/section far wider and taller than
intended — this is exactly how a checklist of small checkmarks turns into a page of giant
icons. Before finishing, check every single CSS rule you wrote for an icon `<svg>` selector
(stat-strip, bullet-list, highlight-list, footer lock icon, etc.) and confirm it either sets
its own `width`/`height` in px or simply leaves sizing to the inline attributes above — never
leave an icon-targeting rule with layout properties only.
Set color via the CSS `color` property on the icon or a wrapper so `currentColor` inherits it.
Icons and charts are SEPARATE systems — never mix them. Copy each icon's inner shapes
(rect/line/circle/path/polyline/polygon) verbatim from the list below; never invent a new
tag name (e.g. `<calendar>` is not a real SVG element), and never paste SVG CHART LIBRARY
syntax (stroke-dasharray, stroke-dashoffset, donut arcs) into an icon glyph.

- check (highlights/checklists):
  <svg ...><polyline points="4 12 9 17 20 6"/></svg>
- trend-up (growth, revenue/margin growth):
  <svg ...><polyline points="3 17 9 11 13 15 21 7"/><polyline points="14 7 21 7 21 14"/></svg>
- dollar (revenue/financial KPIs):
  <svg ...><line x1="12" y1="2" x2="12" y2="22"/><path d="M15.5 8c0-1.7-1.6-3-3.5-3s-3.5 1.3-3.5 3 1.6 2.4 3.5 2.8 3.5 1.1 3.5 2.7-1.6 3-3.5 3-3.5-1.3-3.5-3"/></svg>
- building (properties/facilities/locations):
  <svg ...><rect x="4" y="3" width="16" height="18" rx="1"/><line x1="9" y1="8" x2="9.01" y2="8"/><line x1="15" y1="8" x2="15.01" y2="8"/><line x1="9" y1="13" x2="9.01" y2="13"/><line x1="15" y1="13" x2="15.01" y2="13"/><line x1="10" y1="21" x2="10" y2="17"/><line x1="14" y1="21" x2="14" y2="17"/></svg>
- users (team/HR/management):
  <svg ...><circle cx="9" cy="8" r="3"/><path d="M4 20c0-3 2.5-5 5-5s5 2 5 5"/><circle cx="17" cy="9" r="2.5"/><path d="M15.5 20c.2-2.2 1.7-4 3.8-4.4"/></svg>
- shield (legal/compliance/risk):
  <svg ...><path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z"/></svg>
- calendar (dates/timeline/growth plan):
  <svg ...><rect x="3" y="5" width="18" height="16" rx="2"/><line x1="3" y1="10" x2="21" y2="10"/><line x1="8" y1="3" x2="8" y2="7"/><line x1="16" y1="3" x2="16" y2="7"/></svg>
- map-pin (location):
  <svg ...><path d="M12 21s7-6.5 7-11a7 7 0 1 0-14 0c0 4.5 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/></svg>
- briefcase (operations/products/services):
  <svg ...><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="3" y1="13" x2="21" y2="13"/></svg>
- lock (confidential/footer):
  <svg ...><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V7a4 4 0 1 1 8 0v4"/></svg>
- star (awards/reputation/quality):
  <svg ...><polygon points="12 2 15 9 22 9 16.5 13.5 18.5 21 12 16.5 5.5 21 7.5 13.5 2 9 9 9"/></svg>

═══════════════════════════════════════════════
STEP 2d — SVG CHART LIBRARY (real, data-accurate charts)
═══════════════════════════════════════════════
Whenever a section has 2+ periods of the same metric (e.g. 3 years of revenue) OR a
breakdown into 2+ parts of a whole (e.g. revenue by segment), render it as an inline SVG
chart alongside its [data-table] — never fabricate a chart for data that isn't in the
source, and never let the chart be the ONLY place the exact numbers appear. Compute every
coordinate below from the real extracted numbers — never eyeball or approximate proportions.
Zero external chart libraries, zero <canvas>, zero JS — pure inline <svg>.
Every chart's outer <svg> tag (same rule as ICON SYSTEM, STEP 2c) needs explicit sizing —
either a `width`/`height` attribute or a CSS rule with `width`/`height` (e.g. `width:100%;
height:auto;` so it fills its column) — an <svg> with only a `viewBox` and no `width`/`height`
anywhere still defaults to the browser's native 300×150px, distorting the chart.

▸ [chart-bar] — trend across periods:
  - Fixed box: viewBox="0 0 500 260". Baseline at y=220. Max bar height 180px.
  - For N bars: barWidth = (440/N)*0.6, x_i = 40 + i*(440/N) (i = 0-indexed).
  - For value v and series max `max`: h_i = (v/max)*180, bar y_i = 220 - h_i.
  - Per bar: <rect x="{x_i}" y="{y_i}" width="{barWidth}" height="{h_i}" rx="4" fill="[accent]"/>
    value label above: <text x="{x_i+barWidth/2}" y="{y_i-8}" text-anchor="middle">{value}</text>
    period label below baseline: <text x="{x_i+barWidth/2}" y="242" text-anchor="middle">{period}</text>
  - Baseline: <line x1="30" y1="220" x2="470" y2="220" stroke="[mid]" stroke-width="1"/>
  - Optional: render the most recent period's bar in accent and prior periods in mid (at
    reduced opacity) to draw the eye to the latest figure.

▸ [chart-donut] — composition/mix:
  - Fixed size: viewBox="0 0 200 200". Circle center (100,100), r=70, stroke-width=28, fill="none".
  - Circumference C = 2 × π × 70 ≈ 439.8 — use this constant.
  - For each segment (in order) with percentage p: stroke-dasharray="{(p/100)*C} {C-(p/100)*C}",
    stroke-dashoffset = -(sum of all PRIOR segments' % ) / 100 × C. Wrap all segment <circle>
    elements in one <g transform="rotate(-90 100 100)"> so the first segment starts at 12 o'clock.
  - Worked example, 3 segments at 62.7% / 34.0% / 3.3% (C≈439.8):
    <g transform="rotate(-90 100 100)">
      <circle cx="100" cy="100" r="70" fill="none" stroke-width="28" stroke="[accent]" stroke-dasharray="275.8 164.0" stroke-dashoffset="0"/>
      <circle cx="100" cy="100" r="70" fill="none" stroke-width="28" stroke="[mid]" stroke-dasharray="149.5 290.3" stroke-dashoffset="-275.8"/>
      <circle cx="100" cy="100" r="70" fill="none" stroke-width="28" stroke="[primary]" stroke-dasharray="14.5 425.3" stroke-dashoffset="-425.3"/>
    </g>
  - Center label: <text x="100" y="100" text-anchor="middle" dominant-baseline="middle"> showing
    the single most important % or total.
  - A legend beside/below the donut: one colored dot + label + percentage per segment, in the
    same order and same colors as the arcs.
  - Percentages MUST sum to ~100% and MUST be derived from real source figures (compute from
    raw revenue amounts if the source gives amounts, not %) — never invented splits.

Respect the active template's fill/style rules (STEP 2 palette + any MANDATORY TEMPLATE
OVERRIDE below) when rendering charts and icons: flat single-color fills where a template
forbids gradients/shadows, gradient fills where a template calls for them, etc.

═══════════════════════════════════════════════
STEP 3 — BUILD THE DOCUMENT
═══════════════════════════════════════════════

CRITICAL DATA RULES:
- Copy ALL financial figures, percentages, dates EXACTLY as they appear in the source data — never round, abbreviate, or infer.
- Include ONLY sections where you have actual data — skip sections with no content. If you skip a section, you MUST also remove its entry from the TABLE OF CONTENTS and renumber the remaining Roman numerals — a TOC entry with no matching rendered section is a bug.
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
- Top bar: MUST use `display:flex; justify-content:space-between; align-items:center; width:100%; position:relative; z-index:5`. LEFT side (flex-start): <!-- LOGO --> placeholder (if logo provided) OR empty div. RIGHT side (flex-end): "CONFIDENTIAL INFORMATION MEMORANDUM" in small-caps tracking-widest, accent color, text-align:right. NEVER center either element — logo is strictly left, CIM title is strictly right. Subtle top border in accent color across full width. The top bar's `z-index:5` MUST be higher than any decorative background element on the cover (geometric shapes, gradients, ornamental corner brackets/frames per a template override) — a logo or title rendered behind, touching, or crossing through a decorative line/shape is a critical bug. If this template's cover calls for an ornamental corner frame or brackets, keep them short and inset far enough from the top-left corner (at least the cover's own padding, e.g. 3rem) that they never reach into the top bar's logo/title area.
- CENTER BLOCK (vertically centered, text-align:center, align-items:center — ALL content MUST be centered horizontally):
    • Industry badge pill (e.g. "RESTAURANT & FOOD SERVICE") — accent background, white text, rounded-full, uppercase, letter-spacing, margin:0 auto
    • Business name: massive (4.5rem), white, bold, line-height 1.1, max 2 lines, text-align:center
    • Tasteful thin horizontal rule in accent color below name, width:80px, margin:0 auto
    • Tagline or location if available — rgba(255,255,255,0.85) (NEVER lower opacity — must stay clearly readable against the dark cover background), italic, 1.2rem, text-align:center
    • Asking price block: large accent-colored chip — "Asking Price" label above, price value bold 2.5rem white, centered
- BOTTOM BAR: dark translucent band across full width, at `z-index:2` — same clearance rule as the top bar: this z-index MUST be higher than any decorative background element on the cover, and if this template's cover calls for an ornamental corner frame or brackets, they must stay clear not just of the top bar but of this bottom bar too. The bar's actual rendered height (padding + line-height, typically 50–60px) is almost always taller than a small fixed corner-bracket inset (e.g. 24px) — a frame/bracket inset sized only for the top corners will sit INSIDE the bottom bar's footprint and visibly collide with it. Either size the frame's bottom edge and bottom corner brackets to clear the bottom bar's full rendered height (not the same fixed inset used for the top/left/right), or draw the frame so its perimeter stops above the bottom bar entirely. A footer bar overlapping a decorative corner line is the same class of critical bug as a logo overlapping one:
    • Left: "Prepared exclusively for prospective acquirers" — muted italic
    • Center: current date
    • Right: "STRICTLY PRIVATE & CONFIDENTIAL"
- If images are available AND contextually relevant (property, storefront, food, team — NOT random objects, dice, icons, or unrelated images): use <!-- IMG:1 --> as a CSS background on the cover div. NEVER place it as a <figure> or <img> above the top bar. Implementation: the cover div must have position:relative; overflow:hidden. Inside it, as the very first child, place: <div style="position:absolute;top:0;right:0;bottom:0;left:0;z-index:0;"><img src="..." style="width:100%;height:100%;opacity:0.25;"/></div> followed by a <div style="position:absolute;top:0;right:0;bottom:0;left:0;background:rgba(0,0,0,0.50);z-index:1;"></div>. All cover content sits above both of those: center block and bottom bar at z-index:2, and the top bar at its own z-index:5 as specified above (the top bar's z-index is never lowered to 2 just because an image/overlay is present — 5 stays reserved for it alone so it also clears any decorative shapes, which is why they must never share or exceed it). If the image is not contextually relevant (e.g. dice, generic clipart, icons), skip it entirely — use no image rather than a wrong one.

━━━━━━━━━━━━━━━━━━━━━━━━
PAGE 2 — TABLE OF CONTENTS
━━━━━━━━━━━━━━━━━━━━━━━━
Clean, typographically elegant. Full-page feel (min-height:700px, light background).
- ALL content MUST be left-aligned (text-align:left, align-items:flex-start) — NEVER center the heading or entries.
- Top: "TABLE OF CONTENTS" heading in primary color, large, bold, text-align:left
- Thin accent-colored left border OR top border (not centered decoration)
- Each section: flex row — Roman numeral (accent color, bold, monospace, min-width:3rem), section title (primary, medium weight), dotted leader line flex-grow, page anchor arrow → at right
- Hover state: background tint, cursor pointer
- Subtle section groupings if many sections
- MANDATORY 1:1 MATCH: build the TOC LAST, after you know which sections you actually
  rendered. A section with no real data is skipped entirely per the "Include ONLY
  sections where you have actual data" rule below — when that happens, its TOC entry
  MUST be removed too and the remaining Roman numerals renumbered (I, II, III... with no
  gap). Never leave a TOC entry (e.g. "Appendix") that has no matching section-header/
  section-title actually rendered in the body — every heading promised in the TOC must
  be covered by a real section, and vice versa.

━━━━━━━━━━━━━━━━━━━━━━━━
KEY METRICS STRIP
━━━━━━━━━━━━━━━━━━━━━━━━
Immediately after TOC — a full-width horizontal strip of stat cards (3–6 cards):
- Background: primary color
- Each card: a matching ICON SYSTEM glyph above the label (dollar for revenue/EBITDA, trend-up
  for growth/margin %, building for keys/units/locations, users for headcount, star for rating
  — pick the closest match, skip the icon rather than force a wrong one), centered, white label
  (small, uppercase, muted), large white value (bold, 2rem+), optional unit label
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
- Optional 1-line description: rgba(255,255,255,0.85) (NEVER lower opacity), italic, 0.95rem
- Decorative right-side accent bar or geometric element

SECTION BODY (white or light background, generous padding):
Use the most appropriate layout for the content type:

• EXECUTIVE SUMMARY / INVESTMENT HIGHLIGHTS:
  - Opening paragraph: large pull-quote style (1.15rem, line-height 1.8, border-left 4px accent)
  - Bullet highlights: styled list items, each prefixed by the `check` icon (see ICON SYSTEM)
    in accent color — not a plain text dot

• FINANCIAL TABLES:
  - Full-width table, thead: primary bg, white text
  - Tbody: alternating white / light-bg rows
  - Numbers: right-aligned, monospace font
  - Total/summary rows: bold, accent-tinted background
  - Currency labels: muted, smaller
  - Pair with a [chart-bar] (revenue/EBITDA trend) and/or [chart-donut] (revenue mix) per the
    SVG CHART LIBRARY (STEP 2d) whenever 2+ comparable data points exist — the chart supplements
    the table, it never replaces it

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
SECTION FOOTER (every content section, sections I–X)
━━━━━━━━━━━━━━━━━━━━━━━━
Every section-N div ends with a slim full-width footer bar (padding 0.75rem 3rem, 1px top
border in accent or mid color, background matching the section body — light, not dark):
- Left: business name, small, muted (mid color)
- Center: the `lock` icon (see ICON SYSTEM) + "STRICTLY PRIVATE & CONFIDENTIAL" in small-caps,
  muted — omit the icon (keep the text) if the active template's style rules forbid extra
  ornamentation
- Right: the section's Roman numeral + title, small, muted (e.g. "III. Financial Information")
- One footer per section is enough — there is no true pagination in scrollable HTML, so do not
  repeat it more than once per section-N div
- TEXT CONTRAST rules (below, in TECHNICAL REQUIREMENTS) still apply here — muted must never mean illegible

━━━━━━━━━━━━━━━━━━━━━━━━
LAST PAGE — DISCLAIMER
━━━━━━━━━━━━━━━━━━━━━━━━
Dark footer page (primary bg):
- "IMPORTANT NOTICE & DISCLAIMER" in accent, centered
- Boilerplate confidentiality text: rgba(255,255,255,0.85) — NEVER lower opacity than this, and NEVER a color close in hue/brightness to the background. This text must be plainly, easily readable at a glance, not a subtle/washed-out watermark.
- Centered accent horizontal rule
- CRITICAL: you MUST set this color with a selector that directly targets the <p> tags themselves, e.g. `.disclaimer-text p { color: rgba(255,255,255,0.85); }` — do NOT rely on inheriting color from a parent wrapper. Your global `p { color: ... }` rule (used for light-background body text elsewhere) directly targets every `<p>` element and WILL silently override an inherited color on this dark page, making the text invisible. A directly-matched element selector always wins over an inherited value, regardless of the ancestor's specificity.

═══════════════════════════════════════════════
TECHNICAL REQUIREMENTS
═══════════════════════════════════════════════
- DO NOT add any sticky or fixed navigation bar, top bar, or header bar with section links — no nav element at all.
- Fully self-contained HTML — ALL CSS inside one <style> tag. Zero external resources, CDN links, or web fonts.
- NEVER use CSS custom properties / variables (no :root{} block, no var(--x)). Use hardcoded hex color values everywhere.
- NEVER use clamp() — use fixed rem/px values for font-size and other properties.
- NEVER use the inset shorthand — always use explicit top/right/bottom/left properties.
- NEVER use object-fit — use width:100%;height:100%; with overflow:hidden on the parent instead.
- Font stack: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif
- Max content width: 1000px, centered with auto margins
- The cover page and TOC use fixed-height pages — subsequent sections have generous padding (3rem+)
- Smooth scroll: html { scroll-behavior: smooth }
- Financial numbers: font-variant-numeric: tabular-nums
- Print media: @media print { .no-print { display:none } }
- Page break rules to prevent awkward splits (add these OUTSIDE @media print): h1,h2,h3,h4 { page-break-after: avoid } table,figure,ul,ol { page-break-inside: avoid } tr { page-break-inside: avoid }
- @page { size: A4; margin: 10mm; } must be in the <style> block
- The document should feel HEAVY and SUBSTANTIAL — not lightweight. Use ample whitespace, large typography, rich backgrounds.
- Custom bullet markers: if a ul/li uses a ::before for a styled bullet/dot, you MUST also set list-style:none on that ul/li — otherwise the browser's default bullet renders alongside it, producing a duplicated "• •" marker.
- TEXT CONTRAST (applies everywhere, every template): any text on a colored or dark background must maintain at least a 4.5:1 contrast ratio and must be immediately, plainly readable — never a low-opacity "watermark" effect. On dark backgrounds, text opacity must never go below 0.85 (e.g. rgba(255,255,255,0.85), not 0.6 or 0.7). Never set a text color whose hue/brightness is close to its background — if in doubt, use a plain solid light color (near-white or the palette's accent) rather than a translucent one.
- CSS SPECIFICITY TRAP (a common cause of invisible text — check this every time you write a dark-background block): if you have a global element selector like `p { color: #1a1a1a; }` or `li { color: ... }` for the document's default light-background body text, that rule directly targets every matching tag and OVERRIDES any color merely inherited from a dark-background ancestor (e.g. `.disclaimer-page { color: white; }` does NOT make its `<p>` children white if a global `p { color: #1a1a1a }` rule exists — the direct match always wins over inheritance). Whenever you place text inside a dark/colored block, you MUST set that block's text color with a selector that directly targets the actual text tags (e.g. `.disclaimer-text p { color: ... }`, not just `.disclaimer-text { color: ... }`) — never assume inheritance will apply.
- SVG ICON SIZING (a common, easy-to-miss bug across every template — see ICON SYSTEM, STEP 2c): every icon `<svg>` tag must carry literal `width`/`height` attributes. A CSS rule for an icon selector that only sets layout properties (`flex-shrink`, `margin`, etc.) and forgets `width`/`height` leaves the browser's native SVG default size of 300×150px, ballooning that row/list/section far wider and taller than intended. Check every icon-targeting CSS rule before finishing.
- LOGO / COVER LAYERING: the cover top bar holding the <!-- LOGO --> marker must render at a higher z-index than any decorative background shape, corner frame, or bracket on the cover (see PAGE 1 — COVER for the exact z-index/inset rules) — a logo overlapping or crossing through a decorative line is a critical bug. The SAME rule applies to the bottom bar (date/confidentiality row): a decorative corner frame or bracket sized to clear the top corners is not automatically clear of the bottom corners — check the bottom bar's own rendered height against the frame's bottom inset separately.

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


def _select_template(template_id: str, custom_template: dict | None) -> dict:
    """Pick the active template dict — an uploaded custom template always wins."""
    return custom_template if custom_template else get_template(template_id)


def _build_template_directive(template: dict) -> str:
    """Build a prompt override block for a non-default design template. Empty for 'classic'."""
    if not template.get("palette"):
        return ""

    p = template["palette"]
    f = template["fonts"]
    headings = template.get("headings") or {}
    heading_lines = "\n".join(f'- "{old}" → "{new}"' for old, new in headings.items())
    cover_override = template.get("cover_override") or ""
    section_header_override = template.get("section_header_override") or ""

    return f"""\

═══════════════════════════════════════════════
MANDATORY TEMPLATE OVERRIDE — "{template['name']}"
═══════════════════════════════════════════════
The user explicitly selected this design template. EVERYTHING in this block wins over
any conflicting instruction earlier in this prompt — including STEP 2's industry
fallback palette, the generic font stack in TECHNICAL REQUIREMENTS, and the default
"PAGE 1 — COVER" / "SECTION HEADER" specs. This is not a color-only change: the cover
page and section headers must be STRUCTURALLY different from the default layout, not
just recolored. Ignore any brand-color instructions above — use ONLY the values below.

COLOR PALETTE (use exactly these hex values everywhere primary/accent/light/mid are used):
- primary: {p['primary']}
- accent:  {p['accent']}
- light:   {p['light']}
- mid:     {p['mid']}

FONT STACK (replace the 'Segoe UI' stack from TECHNICAL REQUIREMENTS with these):
- Headings (h1, h2, h3, section titles, cover business name): {f['heading']}
- Body text (paragraphs, lists, table cells): {f['body']}

LAYOUT & STYLE DIRECTION (apply throughout the document):
{template['layout_notes']}

COVER PAGE OVERRIDE (mandatory — do not fall back to the default centered cover spec):
{cover_override}

SECTION HEADER OVERRIDE (mandatory — apply to every section's header band, not just the cover):
{section_header_override}

SECTION HEADING LABELS — rename ONLY the displayed title text, keep the same order,
same Roman numeral, and same underlying content/subtopics:
{heading_lines}
"""


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
    template_id: str = "classic",
    custom_template: dict | None = None,
) -> str:
    """Send all per-file findings + listing context + images to Claude → returns full HTML CIM."""
    client = get_client()
    all_images = all_images or []
    template = _select_template(template_id, custom_template)

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
                "For each image, decide if it adds visual value to the CIM. "
                "ONLY use images that are contextually relevant: property/storefront exterior, food/product shots, team/staff photos, equipment, charts or financials. "
                "SKIP any image that is NOT directly related to the business — e.g. dice, playing cards, random objects, clipart, stock icons, logos of unrelated companies. When in doubt, skip it. "
                f"To embed image N, place the exact marker <!-- IMG:N --> in your HTML where you want it. "
                "The system will replace the marker with the actual image. "
                "Use good placement — inside the relevant section, with surrounding context. "
                "NEVER place an image above the cover top bar or outside the cover's normal content flow."
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

    if brand_primary and brand_accent and template["allow_brand_override"]:
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
        log.debug("LLM: brand colors injected — primary=%s accent=%s", brand_primary, brand_accent)

    logo_instruction = ""
    if logo_b64 and logo_mime:
        logo_instruction = (
            "## Company Logo\n"
            "A company logo is provided below as a base64 image. This is MANDATORY, not optional: "
            "you MUST place the literal marker <!-- LOGO --> in your output HTML, exactly once, "
            "in the cover top bar's LEFT slot. Do not skip it, do not draw a substitute logo "
            "placeholder, do not leave the left slot as an empty div when a logo was provided — "
            "the system searches your HTML for this exact marker and only cleanly injects the "
            "logo there. If the marker is missing, the system force-inserts the logo as a "
            "fallback, which can visually collide with cover decorations (corner brackets, "
            "frames) — always include the marker instead of relying on the fallback.\n"
            "Required top bar structure (adapt colors/fonts to the active template, keep this "
            "exact element/attribute shape):\n"
            '<div style="display:flex;justify-content:space-between;align-items:center;'
            'width:100%;position:relative;z-index:5;"><div><!-- LOGO --></div>'
            '<div style="text-align:right;">CONFIDENTIAL INFORMATION MEMORANDUM</div></div>\n'
            "IMPORTANT: because the logo occupies the top-LEFT, place 'CONFIDENTIAL INFORMATION MEMORANDUM' on the top-RIGHT (text-align:right) in the same flex row — never on the left — so they never overlap. "
            "This top bar row's z-index:5 must sit above any decorative background shapes, corner brackets, or frame borders elsewhere on the cover.\n\n"
        )
        user_content.append({"type": "text", "text": logo_instruction})
        user_content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": logo_mime, "data": logo_b64},
        })

    user_content.append({
        "type": "text",
        "text": (
            f"Today's date is {date.today().strftime('%B %d, %Y')}.\n\n"
            + _HTML_PROMPT
            + _build_template_directive(template)
        ),
    })

    try:
        # Use streaming — HTML generation can exceed 10 minutes with many images
        async with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_HTML_TOKENS,
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            final_msg = await stream.get_final_message()
            html = final_msg.content[0].text
            _add_tokens(final_msg.usage.input_tokens, final_msg.usage.output_tokens)

        html = html.strip()

        # Strip any preamble chatter and/or markdown fencing Claude adds before the
        # actual document (e.g. "I'll create a premium CIM...\n```html\n<!DOCTYPE...").
        # Anchor on the real document boundaries rather than assuming the response
        # starts with a fence — the model doesn't always obey "no explanation".
        doctype_match = re.search(r"<!DOCTYPE\s+html", html, re.IGNORECASE)
        if doctype_match:
            html = html[doctype_match.start():]
        html_end_match = re.search(r"</html\s*>", html, re.IGNORECASE)
        if html_end_match:
            html = html[:html_end_match.end()]
        html = html.strip()
        if html.startswith("```"):
            lines = html.split("\n")
            html = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        if html.endswith("```"):
            html = html.rsplit("```", 1)[0].strip()

        # Replace <!-- LOGO --> marker with actual img tag (Claude places it in cover top-bar left)
        if logo_b64 and logo_mime:
            logo_img = (
                f'<img src="data:{logo_mime};base64,{logo_b64}" alt="Company Logo" '
                f'style="max-height:60px;max-width:180px;object-fit:contain;display:block;"/>'
            )
            if "<!-- LOGO -->" in html:
                html = html.replace("<!-- LOGO -->", logo_img, 1)
            else:
                # Fallback: Claude omitted the marker despite the mandatory instruction —
                # force-inject after <body>. Inset past the ~24px corner-ornament convention
                # templates use (see cover_override frames/brackets in core/templates.py) and
                # give it its own translucent backing chip so it stays legible and visually
                # separated even if it lands near a decorative background line/shape.
                log.error("HTML gen: Claude omitted <!-- LOGO --> marker — using fallback placement")
                logo_tag = (
                    '<div style="position:absolute;top:3rem;left:3rem;z-index:20;'
                    'background:rgba(0,0,0,0.35);border-radius:8px;padding:10px 16px;">'
                    + logo_img + '</div>'
                )
                html = re.sub(r'<body\b[^>]*>', lambda m: m.group(0) + logo_tag, html, count=1)

        # Replace <!-- IMG:N --> markers with actual base64 img tags (sequential over valid images)
        for seq_i, (_orig_i, img) in enumerate(valid_images, start=1):
            marker = f"<!-- IMG:{seq_i} -->"
            if marker in html:
                html = html.replace(marker, _build_image_tag(img, seq_i))

        return html
    except Exception as exc:
        log.error("HTML generation failed: %s", exc)
        raise

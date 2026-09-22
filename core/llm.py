"""Shared Anthropic Claude client + CIM extraction / HTML generation helpers."""

from __future__ import annotations

import asyncio
import base64
import contextvars
import io
import json
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
MAX_HTML_TOKENS = 64000  # Haiku 4.5's actual max_tokens ceiling (verified via Models API)

# Per-pipeline token accumulator. A ContextVar (not a plain global) so that
# concurrent pipeline runs — one asyncio task per listing_id, see
# server._run_job_pipeline — each keep their own counts instead of clobbering
# one shared dict. reset_token_counters() installs a fresh dict at pipeline
# start; tasks spawned afterwards (graph nodes, extraction calls) inherit it
# through the copied context and mutate it in place.
_tokens_var: contextvars.ContextVar[dict[str, int]] = contextvars.ContextVar("cim_token_counts")


def reset_token_counters() -> None:
    _tokens_var.set({"input": 0, "output": 0})


def get_token_counts() -> tuple[int, int]:
    t = _tokens_var.get(None)
    return (t["input"], t["output"]) if t is not None else (0, 0)


def _add_tokens(input_t: int, output_t: int) -> None:
    t = _tokens_var.get(None)
    if t is not None:
        t["input"]  += input_t
        t["output"] += output_t

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

# The custom-template (uploaded PDF/Word/HTML/XML) path's own prompt — used ONLY when the
# user uploaded their own design file (see generate_cim_html's is_custom branch). Carries
# content/data/technical RULES only — never a default visual design. It used to also carry
# (under the old name _HTML_PROMPT) an extremely detailed, opinionated DEFAULT design spec —
# exact cover gradient, a specific TOC style, a hardcoded "full-width band" section-header
# treatment, per-section-type rem sizes and colors — sent alongside an override telling
# Claude to ignore it in favor of the uploaded file. Two competing design instructions in one
# prompt reliably meant Claude honored both at once. Real repro: the Kline Paper CIM (photo
# cover, no color band anywhere in the real file) still came out with a flat gradient cover
# and a full-width colored section-header band, because that default spec was still being
# said out loud even though the override also fired. The uploaded file itself (attached
# separately, see _build_template_reference_blocks) plus the MANDATORY TEMPLATE OVERRIDE
# (_build_template_directive, appended after this prompt) are now the ONLY source of visual
# design for a custom-template job — nothing left in this prompt to compete with them.
_CUSTOM_TEMPLATE_PROMPT = """\
You are building a Confidential Information Memorandum (CIM). The user uploaded their own
design template — attached separately below (as a real vision/document reference, or inlined
as text/markup) — and it is the ONLY source of this document's visual design: cover
composition, color palette, typography, section-header treatment, spacing, decorative
elements, table/list styling, everything visual. There is no default design in this prompt to
fall back on. Study the attached file (and the MANDATORY TEMPLATE OVERRIDE appended after this
prompt, which describes that same file in more structured detail) and reproduce its actual
look, adapting only as needed to fit the real content below — never invent a generic "premium
investment-bank CIM" look of your own.

This is about visual design only. Every rule below — the 10-section content backbone, and
especially the CRITICAL DATA RULES and FINANCIAL NUMBER RULES about never inventing,
rounding, or fabricating a number — is absolute regardless of the uploaded template's design.
Visual fidelity to the upload never means copying its own numbers, names, or wording: every
word and figure in your output comes from the real listing data provided below, never from
the reference file.

═══════════════════════════════════════════════
SECTION LAYOUT COMPONENTS
═══════════════════════════════════════════════
For each section body, pick component(s) from this library based on data volume and type.
Do NOT force a fixed layout — adapt to what the data actually supports, and style every
component to match the uploaded reference file's own visual language (colors, corner radius,
borders, density, decoration), never a generic default look.

COMPONENT LIBRARY:
▸ [stat-strip]      Horizontal KPI cards, each topped with a matching ICON SYSTEM glyph. Use when 3+ numeric metrics exist.
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
ICON SYSTEM
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
4. A section footer, if the reproduced design uses one — the `lock` icon beside any
   "confidential" notice, only where the reference file's own style doesn't forbid
   extra ornamentation.
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
SVG CHART LIBRARY (real, data-accurate charts)
═══════════════════════════════════════════════
Whenever a section has 2+ periods of the same metric (e.g. 3 years of revenue) OR a
breakdown into 2+ parts of a whole (e.g. revenue by segment), render it as an inline SVG
chart alongside its [data-table] — never fabricate a chart for data that isn't in the
source, and never let the chart be the ONLY place the exact numbers appear. Compute every
coordinate below from the real extracted numbers — never eyeball or approximate proportions.
Zero external chart libraries, zero <canvas>, zero JS — pure inline <svg>.
Every chart's outer <svg> tag (same rule as ICON SYSTEM above) needs explicit sizing —
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

Respect the uploaded reference file's own fill/style conventions (per the MANDATORY TEMPLATE
OVERRIDE below) when rendering charts and icons: flat single-color fills where the reference
avoids gradients/shadows, gradient fills where it uses them, etc.

═══════════════════════════════════════════════
BUILD THE DOCUMENT
═══════════════════════════════════════════════

CRITICAL DATA RULES:
- Copy ALL financial figures, percentages, dates EXACTLY as they appear in the source data — never round, abbreviate, or infer.
- Include ONLY sections where you have actual data — skip sections with no content. If you skip a section, you MUST also remove its entry from the TABLE OF CONTENTS and renumber the remaining Roman numerals — a TOC entry with no matching rendered section is a bug.
- Never invent metrics, names, or figures not present in the source data.
- STRICT STRUCTURE: Generate ONLY sections I through X as defined below. Do NOT create any section, heading, or topic outside this list. No bonus sections, no summaries, no additional pages beyond Cover, TOC, sections I–X, and a closing disclaimer page.
- IGNORE internal CRM metadata: do NOT include CRM IDs, usernames, system dates, listing status, campaign IDs, NDA flags, or any other internal admin fields in the document. These are system fields, not business content.
- EMPTY SUBTOPIC RULE: If a subtopic has no data, omit it entirely — do NOT show a heading with empty or placeholder content.
- TABLE OF CONTENTS 1:1 MATCH: build the table of contents LAST, after you know which
  sections you actually rendered. A section with no real data is skipped entirely per the
  rule above — when that happens, its TOC entry MUST be removed too and the remaining Roman
  numerals renumbered (I, II, III... with no gap). Never leave a TOC entry with no matching
  rendered section, and vice versa.
- CLOSING DISCLAIMER: include a final disclaimer/confidentiality page with standard CIM
  legal boilerplate (this is content, not a page beyond the ones listed above) — style it
  consistent with the rest of the reproduced design, never a fixed color scheme of your own.

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

═══════════════════════════════════════════════
TECHNICAL REQUIREMENTS
═══════════════════════════════════════════════
- DO NOT add any sticky or fixed navigation bar, top bar, or header bar with section links — no nav element at all.
- Fully self-contained HTML — ALL CSS inside one <style> tag. Zero external resources, CDN links, or web fonts.
- NEVER use CSS custom properties / variables (no :root{} block, no var(--x)). Use hardcoded hex color values everywhere.
- NEVER use clamp() — use fixed rem/px values for font-size and other properties.
- NEVER use the inset shorthand — always use explicit top/right/bottom/left properties.
- NEVER use object-fit — use width:100%;height:100%; with overflow:hidden on the parent instead.
- Max content width: 1000px, centered with auto margins
- Smooth scroll: html { scroll-behavior: smooth }
- Financial numbers: font-variant-numeric: tabular-nums
- Print media: @media print { .no-print { display:none } }
- Page break rules to prevent awkward splits (add these OUTSIDE @media print): h1,h2,h3,h4 { page-break-after: avoid } table,figure,ul,ol { page-break-inside: avoid } tr { page-break-inside: avoid }
- @page { size: A4; margin: 10mm; } must be in the <style> block
- Custom bullet markers: if a ul/li uses a ::before for a styled bullet/dot, you MUST also set list-style:none on that ul/li — otherwise the browser's default bullet renders alongside it, producing a duplicated "• •" marker.
- TEXT CONTRAST (applies everywhere, every template): any text on a colored or dark background must maintain at least a 4.5:1 contrast ratio and must be immediately, plainly readable — never a low-opacity "watermark" effect. On dark backgrounds, text opacity must never go below 0.85 (e.g. rgba(255,255,255,0.85), not 0.6 or 0.7). Never set a text color whose hue/brightness is close to its background — if in doubt, use a plain solid light color (near-white or the palette's accent) rather than a translucent one.
- CSS SPECIFICITY TRAP (a common cause of invisible text — check this every time you write a dark-background block): if you have a global element selector like `p { color: #1a1a1a; }` or `li { color: ... }` for the document's default light-background body text, that rule directly targets every matching tag and OVERRIDES any color merely inherited from a dark-background ancestor (e.g. `.disclaimer-page { color: white; }` does NOT make its `<p>` children white if a global `p { color: #1a1a1a }` rule exists — the direct match always wins over inheritance). Whenever you place text inside a dark/colored block, you MUST set that block's text color with a selector that directly targets the actual text tags (e.g. `.disclaimer-text p { color: ... }`, not just `.disclaimer-text { color: ... }`) — never assume inheritance will apply.
- SVG ICON SIZING (a common, easy-to-miss bug — see ICON SYSTEM above): every icon `<svg>` tag must carry literal `width`/`height` attributes. A CSS rule for an icon selector that only sets layout properties (`flex-shrink`, `margin`, etc.) and forgets `width`/`height` leaves the browser's native SVG default size of 300×150px, ballooning that row/list/section far wider and taller than intended. Check every icon-targeting CSS rule before finishing.
- LOGO / DECORATIVE LAYERING: if the reproduced cover uses layered decorative elements (shapes, frames, brackets) behind the logo/title/confidentiality text, those text/logo elements must render at a HIGHER z-index than the decorative layer — a logo or title rendered behind, touching, or crossing through a decorative line/shape is a critical bug. Whenever you use position:absolute for a decorative layer, explicitly set and check z-index/stacking for every element that must sit above it.
- ANTI-OVERLAP: every flex/grid child that can hold variable-length text must have `min-width:0` so it can actually shrink instead of overflowing its row; long words must use `overflow-wrap:break-word`. NEVER use position:absolute (or fixed) to place a content image or any other body content — that's reserved for a cover-page decorative/background layer only. Every content image and content element must sit in normal block/flex/grid flow so it can never overlap neighboring text or cards. Place images where they are CONTEXTUALLY relevant (e.g. a property photo near Property Details) and spread them through the document rather than clustering them all together.

Return ONLY the complete HTML document starting with <!DOCTYPE html>. No explanation, no markdown fences."""


_MARKER_PROMPT = """\
You are a world-class investment banking analyst writing the CONTENT of a premium
Confidential Information Memorandum (CIM). This document must read like it came from
Goldman Sachs or Lazard — polished, precise, compelling.

IMPORTANT — you are NOT building a full HTML page. The cover, top/bottom bars, table of
contents, section-header bands, and disclaimer page are all rendered separately by fixed
code from real listing data — you never write any of that. Your entire job is to (1) name
the industry and (2) write the CONTENT of each included section, wrapped in the markers
below. Do not output <!DOCTYPE>, <html>, <head>, <body>, or <style> — none of that exists
in your output.

═══════════════════════════════════════════════
OUTPUT CONTRACT — MARKERS ONLY
═══════════════════════════════════════════════
Emit, in this exact order, nothing else:

1. Exactly once, at the very top:
   <!-- INDUSTRY: Restaurant & Food Service -->
   A short, human-readable industry label (e.g. "Restaurant & Food Service",
   "Hotel & Hospitality", "Technology / SaaS", "Real Estate"). Include a plain-English
   keyword for the business type in the label — it is used downstream to pick a color
   palette, so a vague label like "Business" or "Company" is a bug.

2. If there is at least one real numeric metric to show (revenue, EBITDA, occupancy,
   headcount, etc.), a stat strip wrapped in markers — omit this block entirely if no
   real metrics exist, do NOT invent placeholder stats to fill it:
   <!-- STATS -->
   ...a single C:stat-strip block (see KEY METRICS below)...
   <!-- /STATS -->

3. One block per included section, in Roman-numeral order, contiguously renumbered
   starting at "I" (if you skip a section for lack of data, do NOT leave a gap — e.g. if
   you skip IV, the next section you include becomes "IV", not "V"):
   <!-- SECTION num="I" title="Executive Summary" -->
   ...your section body HTML (see CONTENT SECTIONS below)...
   <!-- /SECTION -->

There is no separate table-of-contents step and no separate footer/disclaimer step —
because the chrome is built directly from the SECTION markers you emit, the TOC and your
sections are always in sync by construction. Do not write a table of contents yourself.

═══════════════════════════════════════════════
CRITICAL — INLINE STYLES ONLY
═══════════════════════════════════════════════
There is no <style> block anywhere in the final document and none of your CSS classes or
ids will have any matching rule — every element you write MUST carry its own complete
`style="..."` attribute for anything that needs to look a particular way. Concretely:
- NEVER write a `<style>` tag, a `class="..."` attribute relied on for appearance, or an
  `id`-based selector. Inline `style="..."` on every element is the only mechanism that
  will render.
- Bullet markers: since `::before` rules don't work without a stylesheet, do NOT rely on
  `<ul><li>` default bullets or `::before` for styled dots/icons. Instead give the `<ul>`
  `style="list-style:none;"` and put the bullet/dot/icon as a real leading element (a
  `<span>` or the icon `<svg>` itself) inside each `<li>`.
- Financial numbers: add `style="font-variant-numeric:tabular-nums;"` (combined with
  whatever other inline styles that element needs) on any element showing a number.
- NEVER use CSS custom properties/`var(--x)`, NEVER use `clamp()`, NEVER use the `inset`
  shorthand (use explicit top/right/bottom/left), NEVER use `object-fit` (use
  `width:100%;height:100%;` with `overflow:hidden` on the parent instead).
- TEXT CONTRAST: any text you place on a colored/dark background in your own freeform HTML
  needs an inline color with at least 4.5:1 contrast — near-white or a light solid color,
  never a low-opacity "watermark" effect, never a hue/brightness close to its background.
  (The 8 C: components handle their own contrast — this applies to freeform HTML only.)
- SVG ICON SIZING: every icon `<svg>` tag must carry literal `width`/`height` attributes
  (not just rely on a class) — otherwise it defaults to the browser's native 300×150px.
- LONG/REAL DATA MUST WRAP, NEVER OVERFLOW: business names, addresses, line-item labels,
  and financial figures come from the real customer's data and their length is unknown to
  you — any element holding one of these values MUST include
  `overflow-wrap:break-word;word-break:break-word;` in its inline style. Never assume a
  name or number is short enough to fit.
- FLEX/GRID CHILDREN MUST SHRINK: the 8 C: components (STEP 2) already bake `min-width:0`
  into every card/column/quadrant they render — you don't need to add it there. But if your
  own freeform HTML (e.g. [image-mosaic]'s multi-column grid) uses `display:flex` or
  `display:grid`, every direct child MUST include `min-width:0` in its inline style, or a
  long real name/number will overflow past its column and collide with the next one.
- NO `position:absolute` IN SECTION/STATS BODY CONTENT: absolute positioning is reserved
  for the cover page chrome you do not write. Never use `position:absolute` (or `fixed`)
  anywhere inside `<!-- STATS -->` or `<!-- SECTION -->` content — it escapes normal
  document flow and is the single most common way body content ends up overlapping
  neighboring text or cards. Keep every freeform layout in normal flex/grid/block flow so
  it grows safely regardless of real content length.

═══════════════════════════════════════════════
STEP 1 — DETECT INDUSTRY
═══════════════════════════════════════════════
Identify the business type from the content. Examples:
Hotel/Hospitality, Restaurant/Food & Beverage, Travel & Tourism, Healthcare, Technology/SaaS,
Retail, Manufacturing, Real Estate, Education, Logistics, Professional Services, E-commerce.

Industry KPIs — use ONLY metrics actually present in the data:
- Hotel: RevPAR, ADR, Occupancy %, Total Keys, F&B Revenue, GOP Margin, Star Rating
- Restaurant: Covers/Day, Avg Check, COGS%, Labour%, Seat Turns, Cuisine, Seating Capacity
- Travel: Booking Volume, Destinations, Repeat Rate, Package Types, Peak Season
- SaaS/Tech: ARR/MRR, Churn, CAC, LTV, NPS, Gross Margin, DAU/MAU
- Healthcare: Patient Volume, Procedures/Day, Payer Mix, Certifications
- Retail: Same-Store Sales, Inventory Turns, SKU Count, Basket Size
- Real Estate: Cap Rate, NOI, Occupancy %, Lease Terms, Price/SqFt

═══════════════════════════════════════════════
STEP 2 — SECTION LAYOUT COMPONENTS
═══════════════════════════════════════════════
Two kinds of content go inside a section body:

1. FREEFORM HTML — plain prose (`<p>`, `<ul>`/`<li>`), and these 4 components, written
   directly as HTML exactly as before:
   ▸ [narrative-pull]  Large pull-quote paragraph with accent left-border. For text-rich, metric-light sections.
   ▸ [image-hero]      Full-width image, max-height 480px. Use for best property/exterior/product shot.
   ▸ [image-mosaic]    2-3 column image gallery. Use when 3+ contextually relevant images exist.
   ▸ [bullet-list]     Styled accent-dot bullet points (or the matching ICON SYSTEM glyph in place of the dot for facilities/amenities/product lists). Use for lists of 5+ items without card structure.

2. COMPONENT BLOCKS — the 8 structural/multi-column/chart layouts below are NEVER written as
   HTML by you. Instead emit a marker of the form:
   <!-- C:type -->
   {...JSON payload with the real extracted data...}
   <!-- /C -->
   A fixed renderer turns this JSON into the actual HTML/CSS/SVG — this is what prevents
   layout overlap on data whose length you can't predict. You supply ONLY real values
   (numbers, labels, text extracted from the source) in the JSON — never markup, never
   invented figures. `type` is one of the 8 names below; payload shape is exact — extra keys
   are ignored, missing required keys drop the whole block silently.

▸ C:stat-strip — 3+ numeric KPI cards. Payload is a JSON array:
  [{"icon":"dollar","label":"Annual Revenue","value":"$2,400,000"}, ...]
  `icon` is one of the ICON SYSTEM names below (STEP 3) or omit it.

▸ C:data-table — any tabular/multi-period financial data:
  {"headers":["Metric","2022","2023","2024"],"rows":[["Revenue","£1.2M","£1.4M","£1.6M"]],"footnote":"optional"}

▸ C:chart-bar — trend across 2+ periods of the same metric (e.g. revenue by year). Supply
  RAW numeric values only — the renderer computes bar heights, scaling, and value-label
  abbreviation itself, and caps display at the most recent 6 periods:
  {"periods":[{"label":"2022","value":1200000},{"label":"2023","value":1400000}],"currency":"£","suffix":""}
  Always pair with a C:data-table showing the exact figures — the chart supplements, never replaces, the table.

▸ C:chart-donut — composition/mix breakdown (e.g. revenue by segment). Supply RAW amounts,
  NOT percentages — the renderer computes percentages and arc geometry from these values,
  so you cannot mis-compute or fabricate a split:
  {"segments":[{"label":"Rooms","value":1800000},{"label":"F&B","value":600000}],"center_label":"optional"}

▸ C:two-col — overview/intro sections needing a narrative + highlight box:
  {"left_html":"<p>...</p>","right_title":"Key Facts","right_items":["Founded 2010","45 employees"],"ratio":"60-40"}
  `left_html` is your own freeform narrative HTML (still prose, still your writing quality);
  `ratio` is "60-40" or "50-50". `right_items` are short real facts, not paragraphs.

▸ C:card-grid — 2+ equal items (team members, features, locations):
  {"cols":2,"cards":[{"title":"Jane Doe","subtitle":"CEO","body":"15 years in hospitality"}]}
  `cols` is 2 (≤4 items) or 3 (5+ items). `subtitle`/`body` are optional per card.

▸ C:swot-grid — SWOT section only:
  {"strengths":["..."],"weaknesses":["..."],"opportunities":["..."],"threats":["..."]}
  Omit a key (or leave it an empty list) if the source has nothing for that quadrant.

▸ C:timeline — growth plans, milestones, roadmap, history:
  {"steps":[{"title":"Phase 1: Regional expansion","body":"optional detail"}]}

SELECTION RULES:
- Sparse data (1-3 points) → [narrative-pull] or C:two-col
- Rich financial data → C:data-table, optionally above/below a C:stat-strip
- Trend across 2+ periods → MUST use C:chart-bar beside its C:data-table; a composition/mix breakdown → MUST use C:chart-donut. Not optional when the data shape exists — skip ONLY if the source has fewer than 2 comparable points. The chart supplements the table, never replaces it
- Team section → C:card-grid (cols 2 for ≤4 people, 3 for 5+)
- Growth/strategy/roadmap → C:timeline
- Never use identical layout for two adjacent sections — vary for visual rhythm
- Combine freely: e.g. a C:stat-strip + a C:two-col + a C:data-table all in one section body

═══════════════════════════════════════════════
STEP 3 — ICON SYSTEM
═══════════════════════════════════════════════
For C:stat-strip (STEP 2), you only ever supply an icon NAME string (e.g. "dollar") in the
JSON payload — the renderer owns the actual SVG markup, so skip straight to STEP 4.

For the two remaining freeform icon uses below, a fixed set of inline monoline SVG icons.
Copy the markup below VERBATIM (only the wrapping <svg> tag's width/height/color may change,
via its own inline `style`) — never redraw a path or invent a new icon shape; hand-drawn path
data renders broken or illegible at this scale.

Icon usage is DELIBERATE, not decorative. Use icons ONLY in these places:
1. Facilities/amenities/product [bullet-list] items — icon replaces the bullet (see INLINE
   STYLES ONLY above — the icon IS the leading element inside the `<li>`, no `::before`).
2. Executive Summary / Investment Highlights list items — the `check` icon replaces the dot.
Do NOT scatter icons through body paragraphs, table cells, or section header bands — that
reads as a generic template, not a bank-grade CIM.

Shared attributes on every icon — these MUST be literal attributes on the <svg> tag itself,
every single time, with NO exceptions: viewBox="0 0 24 24" fill="none" stroke="currentColor"
stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" width="22" height="22".
An inline `style="color:...;"` on the icon or a wrapper sets its color via `currentColor`.
Copy each icon's inner shapes (rect/line/circle/path/polyline/polygon) verbatim from the
list below; never invent a new tag name (e.g. `<calendar>` is not a real SVG element).

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
- star (awards/reputation/quality):
  <svg ...><polygon points="12 2 15 9 22 9 16.5 13.5 18.5 21 12 16.5 5.5 21 7.5 13.5 2 9 9 9"/></svg>

Note: the `lock` icon is NOT yours to place — it belongs to the section-footer band, which
is rendered by the chrome layer, not you.

═══════════════════════════════════════════════
STEP 4 — CHARTS (real, data-accurate)
═══════════════════════════════════════════════
Charts are NOT hand-drawn SVG anymore — you never write chart markup or compute a
coordinate, percentage, or stroke value yourself. Use C:chart-bar (trend across 2+ periods)
or C:chart-donut (composition/mix breakdown) from STEP 2, supplying only the raw real
numbers you extracted. The renderer computes all geometry, scaling, and percentages from
those raw values — this is deliberate: it removes any chance of you mis-computing or
fabricating a proportion. Whenever the source data has this shape (2+ real comparable
periods, or a real breakdown into parts of a whole), the chart is MANDATORY, not a
nice-to-have — do not settle for a C:data-table alone when a chart is possible. Never
fabricate a chart for data that isn't in the source (skip it if fewer than 2 comparable
points exist), and always pair a chart with its C:data-table so the exact figures are also
shown in full.

═══════════════════════════════════════════════
STEP 5 — WRITE THE CONTENT
═══════════════════════════════════════════════

CRITICAL DATA RULES:
- Copy ALL financial figures, percentages, dates EXACTLY as they appear in the source data — never round, abbreviate, or infer.
- Include ONLY sections where you have actual data — skip sections with no content (renumber contiguously, see OUTPUT CONTRACT above).
- Never invent metrics, names, or figures not present in the source data.
- STRICT STRUCTURE: Generate ONLY sections I through X as defined below. Do NOT create any section, heading, or topic outside this list. No bonus sections, no summaries, no additional pages.
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
KEY METRICS (the <!-- STATS --> block, if used)
━━━━━━━━━━━━━━━━━━━━━━━━
The entire <!-- STATS --> block is a single C:stat-strip (STEP 2) — 3-6 cards, one JSON
array entry each: {"icon":"dollar","label":"...","value":"..."}. Pick the closest icon match
(dollar for revenue/EBITDA, trend-up for growth/margin %, building for keys/units/locations,
users for headcount, star for rating) or omit `icon` rather than force a wrong one. Only use
metrics actually present in the data.

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
SECTION BODY CONTENT PATTERNS
━━━━━━━━━━━━━━━━━━━━━━━━
This is everything that goes inside a `<!-- SECTION -->...<!-- /SECTION -->` block. Do NOT
include a header band, footer bar, or title heading of your own — the chrome layer wraps
your body content with the section's numbered header band and footer automatically from the
`num`/`title` attributes you put on the marker. Start straight into the content:

• EXECUTIVE SUMMARY / INVESTMENT HIGHLIGHTS:
  - Opening paragraph: large pull-quote style (inline style: font-size:1.15rem, line-height:1.8, border-left:4px solid [accent-ish], padding-left:1.5rem)
  - Bullet highlights: styled list items, each prefixed by the `check` icon in an accent-ish color — not a plain text dot

• FINANCIAL TABLES:
  - Emit a C:data-table block (STEP 2) with the exact source figures as headers/rows.
  - MUST pair with a C:chart-bar (revenue/EBITDA trend) and/or C:chart-donut (revenue mix)
    whenever 2+ comparable real data points exist for that metric — this is not optional
    when the data supports it. Only skip the chart if the source genuinely has fewer than
    2 comparable points (nothing to plot). The chart supplements the table, it never
    replaces it.

• IMAGE PLACEMENT (<!-- IMG:N -->):
  - Hero/exterior/product shots: full-width poster format (inline style: max-height:480px, width:100%, overflow:hidden wrapper, border-radius:12px, box-shadow:0 8px 32px rgba(0,0,0,0.18))
  - Interior/detail shots: 2-column grid if 2+ images available (inline style: display:flex, gap:1.5rem)
  - Team/headshot photos: circular crop (inline style: border-radius:50%, width/height:120px, overflow:hidden wrapper), centered
  - Each image: a `<figcaption>` below in muted italic inline style
  - Place images where they are CONTEXTUALLY relevant — property photo near Property Details, food shots near Menu/Concept section, etc.
  - DO NOT cluster all images together — spread them throughout the document
  - Use <!-- IMG:N --> markers generously if images are available — they make the CIM dramatically more compelling

• TWO-COLUMN LAYOUT (for details/overview sections):
  - Emit a C:two-col block (STEP 2) — `left_html` is your narrative prose, `right_items`
    are real key facts (not paragraphs).

• SWOT (if data available):
  - Emit a single C:swot-grid block (STEP 2) with the 4 quadrants — colors and layout are
    fixed by the renderer.

• MANAGEMENT TEAM (if data available):
  - Emit a C:card-grid block (STEP 2): cols 2 for ≤4 people, cols 3 for 5+.

• GROWTH & STRATEGY / INVESTMENT THESIS:
  - Emit a C:timeline block (STEP 2) with one step per milestone/phase.

Return ONLY the markers and their content, in the exact order specified in OUTPUT CONTRACT
above. No explanation, no markdown fences, no <!DOCTYPE>/<html>/<head>/<body>/<style> tags."""


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


def _format_design_audit(audit: dict) -> str:
    """Render the structured dict from audit_template_design() into readable
    prose for the prompt. Defensive against partial/malformed shapes — this
    is LLM output stored as opaque JSON (core/template_store.py), so a field
    or sub-key can be missing or a saved template can predate this feature
    entirely; a missing piece is simply omitted, never a crash."""
    def _get(section: str, key: str) -> str:
        val = (audit.get(section) or {}).get(key) if isinstance(audit.get(section), dict) else None
        return str(val) if val else ""

    def _get_bool(section: str, key: str) -> str:
        # Separate from _get(): a real `False` is meaningful data, not a
        # missing value — _get()'s "falsy means unset" rule would silently
        # swallow it, which is correct for free-text fields but wrong here.
        sect = audit.get(section)
        val = sect.get(key) if isinstance(sect, dict) else None
        return {"True": "yes", "False": "no"}.get(str(val), "") if isinstance(val, bool) else ""

    lines = []
    has_image = _get_bool("cover", "has_image")
    cover_bits = [
        _get("cover", "layout"), _get("cover", "background"),
        _get("cover", "decorative_elements"), _get("cover", "title_treatment"), has_image,
    ]
    if any(cover_bits):
        lines.append(
            f"- Cover: layout={_get('cover', 'layout') or 'n/a'}; "
            f"background={_get('cover', 'background') or 'n/a'}; "
            f"decorative elements={_get('cover', 'decorative_elements') or 'n/a'}; "
            f"title treatment={_get('cover', 'title_treatment') or 'n/a'}; "
            f"has its own image={has_image or 'n/a'}"
        )
    if any([_get("typography", "heading_font_style"), _get("typography", "body_font_style")]):
        lines.append(
            f"- Typography: headings={_get('typography', 'heading_font_style') or 'n/a'}; "
            f"body={_get('typography', 'body_font_style') or 'n/a'}; "
            f"case={_get('typography', 'heading_case') or 'n/a'}; "
            f"letter-spacing={_get('typography', 'letter_spacing') or 'n/a'}"
        )
    if _get("colors", "notes"):
        lines.append(f"- Color usage notes: {_get('colors', 'notes')}")
    if any([_get("section_headers", "style"), _get("section_headers", "decoration")]):
        lines.append(
            f"- Section headers: style={_get('section_headers', 'style') or 'n/a'}; "
            f"alignment={_get('section_headers', 'alignment') or 'n/a'}; "
            f"decoration={_get('section_headers', 'decoration') or 'n/a'}"
        )
    body_bits = [_get("body_style", k) for k in
                 ("density", "corner_style", "shadows", "table_style", "list_style", "dividers")]
    if any(body_bits):
        lines.append(
            f"- Body style: density={_get('body_style', 'density') or 'n/a'}; "
            f"corners={_get('body_style', 'corner_style') or 'n/a'}; "
            f"shadows={_get('body_style', 'shadows') or 'n/a'}; "
            f"tables={_get('body_style', 'table_style') or 'n/a'}; "
            f"lists={_get('body_style', 'list_style') or 'n/a'}; "
            f"dividers={_get('body_style', 'dividers') or 'n/a'}"
        )
    motifs = audit.get("distinctive_motifs")
    if motifs and str(motifs).strip().lower() not in ("none", "n/a", ""):
        lines.append(f"- Distinctive motifs: {motifs}")
    return "\n".join(lines)


_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _valid_hex(value: Any) -> str | None:
    """Return value if it's a genuine "#rrggbb" hex string, else None. Guards
    against the audit LLM writing "not visible" or "n/a" into a color field
    despite the schema instructions — colors are almost always visible, but
    never trust free-form LLM output to already be a valid hex code."""
    if isinstance(value, str) and _HEX_COLOR_RE.match(value.strip()):
        return value.strip()
    return None


def _clean_audit_val(val: Any) -> str:
    """Normalize one design-audit leaf value to a usable string, or '' if the
    audit LLM marked it not applicable ('none' / 'n/a' / 'not visible') —
    same filtering _format_design_audit uses, factored out so the directive
    builders below can reuse it."""
    s = str(val).strip() if val not in (None, "") else ""
    return "" if s.lower() in ("none", "n/a", "not visible", "false") else s


_FILL_KEYWORDS = (
    "band", "gradient", "solid color", "solid-color", "colored background",
    "color fill", "coloured background", "filled banner", "solid fill", "full-bleed color",
)


def _mentions_color_fill(text: str) -> bool:
    """True if a design-audit description already calls for some kind of
    solid/gradient color fill — used to decide whether the MANDATORY override
    block needs to explicitly rule a fill OUT. Originally added when the
    prompt still carried its own default "full-width band" section-header
    spec alongside the override, which Claude followed even when the override
    described something else (a bordered box never said "no band", so Claude
    kept both). That default spec is gone now (see _CUSTOM_TEMPLATE_PROMPT's
    own comment), but the explicit negation stays as a guard against the
    model's own general trained-in habit of reaching for a colored band/
    gradient CIM look when the reference doesn't call for one."""
    lowered = text.lower()
    return any(kw in lowered for kw in _FILL_KEYWORDS)


def _audit_cover_directive(audit: dict | None) -> str:
    """Build the COVER PAGE OVERRIDE text from the vision-based design audit
    (audit_template_design) instead of the deterministic PDF heuristic in
    core/pdf_style_extractor.py. The deterministic extractor can only detect
    "some non-black/white vector fill exists somewhere" and always describes
    the cover as sitting on a solid color band — wrong for any real template
    whose cover is a photo, gradient, or a bordered frame over an image
    (exactly the Kline Paper template that exposed this: the audit correctly
    saw a full-bleed photo, but the deterministic band heuristic picked up
    the border-frame stroke and told Claude to render a flat color band
    instead, and that wrong-but-forcefully-worded MANDATORY instruction won).
    The audit actually looked at the real pages, so it wins wherever it
    captured something; the deterministic text is now only a fallback for
    templates with no audit at all (e.g. audit_template_design failed)."""
    cover = (audit or {}).get("cover")
    if not isinstance(cover, dict):
        return ""
    bits = []
    layout = _clean_audit_val(cover.get("layout"))
    if layout:
        bits.append(f"Layout: {layout}.")
    background = _clean_audit_val(cover.get("background"))
    if background:
        bits.append(f"Background: {background}.")
    decorative = _clean_audit_val(cover.get("decorative_elements"))
    if decorative:
        bits.append(f"Decorative elements: {decorative}.")
    title = _clean_audit_val(cover.get("title_treatment"))
    if title:
        bits.append(f"Title treatment: {title}.")
    if cover.get("has_image") is True:
        bits.append(
            "The real cover uses an actual photographic image, not a flat color or gradient "
            "fill — reproduce a genuine photographic cover (a fitting real photo as the cover "
            "background/element, e.g. from the listing images provided), never substitute a "
            "solid-color or gradient block for it."
        )
        if not _mentions_color_fill(background):
            bits.append(
                "Do NOT default to a flat gradient/solid-color cover background — this "
                "template's real cover has no such fill at all. If no suitable real photo is "
                "available among the provided listing images, use a plain light or white "
                "background instead of a gradient — never fabricate a gradient cover as a "
                "substitute for the missing photo."
            )
    return " ".join(bits)


def _audit_section_header_directive(audit: dict | None) -> str:
    """Section-header equivalent of _audit_cover_directive — see its docstring."""
    sh = (audit or {}).get("section_headers")
    if not isinstance(sh, dict):
        return ""
    bits = []
    style = _clean_audit_val(sh.get("style"))
    if style:
        bits.append(f"Style: {style}.")
    alignment = _clean_audit_val(sh.get("alignment"))
    if alignment:
        bits.append(f"Alignment: {alignment}.")
    decoration = _clean_audit_val(sh.get("decoration"))
    if decoration:
        bits.append(f"Decoration: {decoration}.")
    if not bits:
        return ""
    if not _mentions_color_fill(" ".join(bits)):
        bits.append(
            "Do NOT use a full-width solid-color or gradient background band behind section "
            "headers — this template's real section headers have no colored fill behind them "
            "at all; use ONLY the treatment described above (e.g. a bordered box/rule directly "
            "on the page background)."
        )
    return " ".join(bits)


def _audit_layout_directive(audit: dict | None) -> str:
    """Body/layout equivalent of _audit_cover_directive: whitespace density,
    corners, shadows, table/list style, dividers, plus any distinctive
    motifs the audit called out — richer and more accurate than the
    deterministic layout_notes (which only knows a detected bullet glyph and
    whether some vector fill exists)."""
    audit = audit or {}
    bs = audit.get("body_style")
    bits = []
    if isinstance(bs, dict):
        for key, label in (
            ("density", "Whitespace/density"), ("corner_style", "Corners"),
            ("shadows", "Shadows"), ("table_style", "Tables"),
            ("list_style", "Lists"), ("dividers", "Dividers"),
        ):
            val = _clean_audit_val(bs.get(key))
            if val:
                bits.append(f"{label}: {val}.")
    motifs = _clean_audit_val(audit.get("distinctive_motifs"))
    if motifs:
        bits.append(f"Distinctive motifs to reproduce: {motifs}.")
    return " ".join(bits)


def _build_template_directive(template: dict) -> str:
    """Build a prompt override block for a non-default design template. Empty for 'classic'."""
    if not template.get("palette"):
        return ""

    p = template["palette"]
    f = template["fonts"]
    headings = template.get("headings") or {}
    heading_lines = "\n".join(f'- "{old}" → "{new}"' for old, new in headings.items())

    design_audit = template.get("design_audit")
    cover_override = _audit_cover_directive(design_audit) or template.get("cover_override") or ""
    section_header_override = (
        _audit_section_header_directive(design_audit) or template.get("section_header_override") or ""
    )
    layout_notes = _audit_layout_directive(design_audit) or template.get("layout_notes") or ""

    audit_block = ""
    # The audit's own color estimate (a vision-based judgment looking at the
    # actual rendered design) is generally more reliable than the deterministic
    # extractor's heuristic (most-common heading/body run color, or most-common
    # vector-fill color) — which can miss gradients entirely or pick up an
    # incidental color. Override the deterministic palette field-by-field
    # wherever the audit supplied a genuine hex value; fall back to the
    # deterministic value per-field otherwise, so a partial audit (e.g. only
    # 2 of 4 colors given) never loses the other 2 known-good values.
    audit_colors = (design_audit or {}).get("colors") if isinstance(design_audit, dict) else None
    audit_colors = audit_colors if isinstance(audit_colors, dict) else {}
    primary = _valid_hex(audit_colors.get("primary_hex")) or p["primary"]
    accent = _valid_hex(audit_colors.get("accent_hex")) or p["accent"]
    light = _valid_hex(audit_colors.get("background_hex")) or p["light"]
    mid = _valid_hex(audit_colors.get("mid_hex")) or p["mid"]

    if isinstance(design_audit, dict):
        formatted = _format_design_audit(design_audit)
        if formatted:
            audit_block = f"""
AUDITED DESIGN SPECIFICATION (from a dedicated design-audit pass over the uploaded file —
this is far richer than the 4 hex colors and layout notes below and is the SINGLE MOST
AUTHORITATIVE source for the uploaded template's actual design; the palette/layout-notes
below only fill in whatever this doesn't cover. The COLOR PALETTE below already uses this
audit's own color estimate wherever it gave one):
{formatted}
"""

    return f"""\

═══════════════════════════════════════════════
MANDATORY TEMPLATE OVERRIDE — "{template['name']}"
═══════════════════════════════════════════════
This is the actual design of the user's uploaded file (attached earlier as a real
vision/document reference, or inlined as text/markup) — the sole visual design target for
this document. The cover, section headers, and overall document structure must actually
resemble this file's real composition, not a generic default shape merely recolored to
match. The 4 hex values and layout notes below are a deterministic SUMMARY of that same
uploaded file; they are necessarily incomplete (a palette can't describe "no decorative
shapes" or "a two-column cover"), so treat the actual attached file/image as authoritative
for anything this summary doesn't capture.
{audit_block}
COLOR PALETTE (use exactly these hex values everywhere primary/accent/light/mid are used):
- primary: {primary}
- accent:  {accent}
- light:   {light}
- mid:     {mid}

FONT STACK:
- Headings (h1, h2, h3, section titles, cover business name): {f['heading']}
- Body text (paragraphs, lists, table cells): {f['body']}

LAYOUT & STYLE DIRECTION (apply throughout the document):
{layout_notes}

COVER PAGE (mandatory):
{cover_override}

SECTION HEADER TREATMENT (mandatory — apply to every section, not just the cover):
{section_header_override}

SECTION HEADING LABELS — rename ONLY the displayed title text, keep the same order,
same Roman numeral, and same underlying content/subtopics:
{heading_lines}
"""


def _build_marker_heading_directive(template: dict) -> str:
    """Marker-branch equivalent of _build_template_directive: content-only (section title
    wording), since palette/fonts/cover/section-header markup are now chrome's job."""
    headings = template.get("headings") or {}
    if not headings:
        return ""
    heading_lines = "\n".join(f'- "{old}" → "{new}"' for old, new in headings.items())
    return f"""\

═══════════════════════════════════════════════
MANDATORY TEMPLATE OVERRIDE — "{template['name']}"
═══════════════════════════════════════════════
The user selected this design template, which renames section titles. Use these titles
as the `title="..."` attribute on the matching SECTION marker — same order, same Roman
numeral, same underlying content/subtopics, only the displayed name changes:
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


def _build_template_reference_blocks(file_b64: str, file_ext: str, intro_text: str) -> list[dict]:
    """Build the content blocks that let Claude actually see/read an uploaded
    template file. Shared by generate_cim_html's design-reference attachment
    and audit_template_design's dedicated design-audit call so the two never
    drift out of sync on how each format is attached.

    PDF -> real document (vision) block, Claude sees the actual pages.
    .docx/.pptx -> flattened text + any embedded images as real vision blocks
    (extract_reference_images) — neither format has a document-vision path.
    HTML/XML -> raw markup as text (already carries the real CSS values).
    """
    blocks: list[dict] = []
    if file_ext == "pdf":
        blocks.append({"type": "text", "text": intro_text})
        blocks.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": file_b64},
        })
        return blocks

    raw_bytes = base64.standard_b64decode(file_b64)
    ref_images: list[dict] = []
    source_label = "template"
    if file_ext in ("docx", "doc"):
        from core.docx_style_extractor import extract_plain_text, extract_reference_images
        raw_text = extract_plain_text(raw_bytes)
        source_label = "Word template"
        try:
            ref_images = extract_reference_images(raw_bytes)
        except Exception as exc:
            log.error("Template reference: failed to extract images from .docx: %s", exc)
    elif file_ext in ("pptx", "ppt"):
        from core.pptx_style_extractor import extract_plain_text, extract_reference_images
        raw_text = extract_plain_text(raw_bytes)
        source_label = "PowerPoint template"
        try:
            ref_images = extract_reference_images(raw_bytes)
        except Exception as exc:
            log.error("Template reference: failed to extract images from .pptx: %s", exc)
    else:  # html, htm, xml
        raw_text = raw_bytes.decode("utf-8", errors="ignore")

    blocks.append({"type": "text", "text": intro_text + f"\n\n```\n{raw_text}\n```"})
    for ref_img in ref_images:
        blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": ref_img["mime"], "data": ref_img["b64"]},
        })
        blocks.append({
            "type": "text",
            "text": (
                f"(Above is an image embedded in the uploaded {source_label} — design "
                "reference only: mimic its visual style if relevant, never copy any "
                "text/logo/figures from it into the output.)"
            ),
        })
    return blocks


_TEMPLATE_AUDIT_PROMPT = """\
Describe ONLY the visual design of the attached template file — never its text content,
never its numbers, never any company/business name it contains. Look at it the way a
graphic designer would: composition, color, type, spacing, decoration.

Return ONLY a single JSON object, no markdown fences, no explanation, matching exactly
this schema (use "none" or "not visible" for any field that genuinely doesn't apply —
never invent a detail you can't actually see):

{
  "cover": {
    "layout": "e.g. centered / left-aligned / split-image / full-bleed-image / grid",
    "background": "e.g. solid navy / diagonal gradient navy-to-gold / full-bleed photo / plain white",
    "decorative_elements": "e.g. thin gold corner frame / large translucent circles / none",
    "title_treatment": "size/weight/case as observed, e.g. large bold uppercase serif, centered",
    "has_image": true or false
  },
  "typography": {
    "heading_font_style": "e.g. bold serif with wide letter-spacing / condensed sans-serif",
    "body_font_style": "e.g. plain sans-serif, comfortable line-height",
    "heading_case": "e.g. uppercase / title case / sentence case",
    "letter_spacing": "e.g. tight / normal / wide-tracking"
  },
  "colors": {
    "primary_hex": "#rrggbb best estimate of the dominant dark/brand color",
    "accent_hex": "#rrggbb best estimate of the accent/highlight color",
    "background_hex": "#rrggbb best estimate of the page background",
    "mid_hex": "#rrggbb best estimate of a secondary/muted tone for less prominent text or chart elements",
    "notes": "anything about color usage a hex code alone can't capture, e.g. 'gold used only for rule lines, never as a fill'"
  },
  "section_headers": {
    "style": "e.g. full-width colored band / plain with a thin rule beneath / no visual separation at all",
    "alignment": "left or center",
    "decoration": "e.g. small numbered chip before the title / none"
  },
  "body_style": {
    "density": "e.g. generous whitespace / compact and dense",
    "corner_style": "sharp corners / rounded corners",
    "shadows": "flat, no shadows / subtle drop shadows / heavy shadows",
    "table_style": "e.g. no visible table borders, alternating row tint / bordered grid",
    "list_style": "e.g. simple dash bullets / numbered / no lists present",
    "dividers": "e.g. thin horizontal rules between sections / none"
  },
  "distinctive_motifs": "free text — anything signature/unusual about this specific template that the fields above don't capture, or 'none' if the design is plain"
}
"""


async def audit_template_design(file_b64: str, file_ext: str) -> dict | None:
    """Dedicated design-audit call, run ONCE at template-upload time (see
    server.py's /template/upload) — not per-generation. Its sole job is to
    study the uploaded file and describe its actual visual design in
    exhaustive, structured detail, producing a far richer design source than
    the deterministic palette/font extraction in core/*_style_extractor.py
    (which literally cannot express things like "no decorative shapes" or
    "split-image cover layout" — it only sees a handful of colors/fonts).

    The result gets merged into the saved custom_template dict as
    "design_audit" (see server.py) and, from there, round-trips transparently
    through core/template_store.py's JSON persistence and the frontend's
    opaque custom_template pass-through — audited once, reused free on every
    future generation from that template, including saved-template reuse.

    Returns None on any failure (bad JSON, API error, unreadable file) —
    generation still works via the existing deterministic extraction; this
    is a fidelity upgrade, never a hard dependency, so a failure here must
    never fail the upload itself.
    """
    if not file_b64:
        return None

    intro = (
        "## Template File to Audit\n"
        "This file is a design template uploaded by a user. You are auditing its VISUAL "
        "DESIGN ONLY — see the prompt that follows for the exact output format."
    )
    try:
        content_blocks = _build_template_reference_blocks(file_b64, file_ext, intro)
    except Exception as exc:
        log.error("Template audit: failed to build reference blocks (ext=%s): %s", file_ext, exc)
        return None
    content_blocks.append({"type": "text", "text": _TEMPLATE_AUDIT_PROMPT})

    try:
        client = get_client()
        response = await client.messages.create(
            model=MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": content_blocks}],
        )
        _add_tokens(response.usage.input_tokens, response.usage.output_tokens)
        raw = response.content[0].text.strip()
    except Exception as exc:
        log.error("Template audit: LLM call failed: %s", exc)
        return None

    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0].strip()

    try:
        audit = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.error("Template audit: response was not valid JSON: %s", exc)
        return None

    if not isinstance(audit, dict):
        log.error("Template audit: response JSON was not an object")
        return None
    return audit


async def generate_cim_html(
    all_findings: list[str],
    listing_xml: str,
    listing_name: str,
    asking_price: str,
    all_images: list[dict] | None = None,
    featured_image: dict | None = None,
    logo_b64: str = "",
    logo_mime: str = "",
    brand_primary: str = "",
    brand_accent: str = "",
    template_id: str = "classic",
    custom_template: dict | None = None,
) -> str:
    """Send all per-file findings + listing context + images to Claude.

    If custom_template is set (PDF-upload flow): returns a full HTML document, exactly
    as today. Otherwise (one of the 5 built-in templates): returns marker-delimited
    text (industry + section content only) — the caller (formatter_node) is
    responsible for passing it through core.cim_assembler.assemble() to get the
    final HTML. See docs/superpowers/specs/2026-08-31-cim-chrome-templates-design.md.
    """
    client = get_client()
    all_images = all_images or []
    is_custom = custom_template is not None
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

    # The user-uploaded "featured" image (server.py's /generate-cim request field
    # featured_image) is different from the pool above: the user explicitly chose
    # it to appear in this CIM, so it is NOT skippable — Claude picks WHERE it
    # fits best, never WHETHER to use it. It gets the next sequential IMG index
    # after the general pool and rides the exact same <!-- IMG:N --> substitution
    # mechanism below, which is what keeps its placement a normal-flow, non-
    # overlapping <figure> rather than arbitrary markup. If Claude still omits
    # the marker, the fallback further below force-places it — this field is
    # enforced in code, not requested by prompt alone.
    featured_index: int | None = None
    featured_img_data: dict | None = None
    if featured_image:
        f_b64 = featured_image.get("b64", "")
        f_mime = featured_image.get("mime", "image/jpeg")
        f_label = featured_image.get("label") or "Featured photo"
        if f_b64 and _is_valid_image(f_b64, f_mime, f_label):
            featured_index = len(valid_images) + 1
            featured_img_data = {"b64": f_b64, "mime": f_mime, "label": f_label}
            user_content.append({
                "type": "text",
                "text": (
                    f"## Featured Image — MANDATORY (Image {featured_index})\n"
                    "The user explicitly uploaded this photo to be featured in this CIM. "
                    "Unlike the images above, this one must NOT be skipped and must NOT be "
                    "judged for relevance — place it. "
                    "Choose the ONE content section it fits best based on what it actually "
                    "shows (exterior/storefront -> Business/Property Overview; product or "
                    "food shot -> Products & Services; team photo -> Management & Team; "
                    "equipment/interior -> Operations). "
                    f"Place the exact marker <!-- IMG:{featured_index} --> exactly once, using "
                    "the [image-hero] pattern (full-width, inside that section's normal content "
                    "flow) so it can never overlap anything else. "
                    "Do NOT place it on the cover, do NOT wrap it in position:absolute, and do "
                    "NOT invent a caption beyond a short factual label of what it depicts."
                ),
            })
            user_content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": f_mime, "data": f_b64},
            })
            user_content.append({
                "type": "text",
                "text": (
                    f"(Above is the mandatory featured image — Image {featured_index}. "
                    f"Use marker <!-- IMG:{featured_index} --> exactly once.)"
                ),
            })
            valid_images.append((0, featured_img_data))
        else:
            log.error("HTML gen: dropping invalid/corrupt featured_image")

    # For the 5 built-in templates, palette (including any brand-color override) and
    # the logo placement are entirely chrome's job now (core/chrome/<id>.py +
    # core/cim_assembler.py) — Claude never sees brand colors or the logo image in
    # that branch, since it no longer authors any cover/header markup that would use
    # them. The PDF-upload custom_template flow is unchanged.
    if is_custom and brand_primary and brand_accent and template["allow_brand_override"]:
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
    if is_custom and logo_b64 and logo_mime:
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

    # Raw uploaded template file, if the client round-tripped it (see
    # server.py's /template/upload, core/template_extractor.py). Runs
    # alongside the deterministic palette/font/layout_notes extraction above,
    # not instead of it — it's the same file, this just gives Claude the
    # actual thing to look at for anything the deterministic pass can't
    # capture (table borders, multi-column layout, decorative shapes). Older
    # custom_template dicts (extracted before this field existed) simply
    # won't have file_b64, so this block is skipped and behavior is unchanged.
    #
    # Only PDF gets a true vision/document attachment — Claude's document
    # content block only accepts application/pdf. Word/HTML/XML have no such
    # vision path, so their real text/markup is inlined as a plain text block
    # instead (still lets Claude read the actual structure, just not "see" it).
    file_b64 = template.get("file_b64")
    file_ext = template.get("file_ext", "")
    if is_custom and file_b64:
        design_ref_intro = (
            "## Uploaded Template File — THE design target (reference ONLY, never content)\n"
            "The file the user uploaded as their design template is attached/quoted below. "
            "This is the primary source of truth for what the generated CIM should look "
            "like — study its actual layout: cover composition, whether it's centered or "
            "not, whether it has any decorative shapes at all, section header treatment, "
            "table/list structures, spacing/density, typographic scale, image placement — "
            "and reproduce THAT, not this prompt's own default design (see the PRIORITY "
            "ORDER note at the top of this prompt — the 'PAGE 1 — COVER' etc. specs below "
            "are fallback only). The palette/fonts/layout notes elsewhere in this prompt are "
            "a deterministic summary of this same file; treat this attachment as the "
            "authoritative reference wherever that summary underspecifies something — a "
            "handful of hex codes and font names cannot capture a layout.\n"
            "CRITICAL: this file is a DESIGN REFERENCE ONLY. Never copy any text, numbers, "
            "company name, or figures from it into your output — every word and number you "
            "write must come from the listing data / findings / images provided above. Mimic "
            "the LOOK of the uploaded template, never its CONTENT."
        )
        user_content.extend(_build_template_reference_blocks(file_b64, file_ext, design_ref_intro))

    if is_custom:
        prompt_text = _CUSTOM_TEMPLATE_PROMPT + _build_template_directive(template)
    else:
        prompt_text = _MARKER_PROMPT + _build_marker_heading_directive(template)

    user_content.append({
        "type": "text",
        "text": (
            f"Today's date is {date.today().strftime('%B %d, %Y')}.\n\n"
            + prompt_text
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

        if final_msg.stop_reason == "max_tokens":
            log.error(
                "generate_cim_html: output truncated at MAX_HTML_TOKENS=%d (output_tokens=%d) — "
                "listing content likely too long, response may be missing sections",
                MAX_HTML_TOKENS, final_msg.usage.output_tokens,
            )

        html = html.strip()

        if is_custom:
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

        if is_custom:
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
        # else: the marker branch never asks Claude to place a logo at all — the logo
        # <img> is built directly from real logo_b64/logo_mime by core/cim_assembler.py
        # and handed to the chrome renderer, so there's no marker to substitute here
        # and no fallback-placement bug class to have.

        # Snapshot before substitution — once the loop below replaces a marker with
        # its <figure>, the marker string is gone from html either way, so "was it
        # actually there" can only be checked now.
        featured_marker = f"<!-- IMG:{featured_index} -->" if featured_index else None
        featured_used_by_llm = bool(featured_marker and featured_marker in html)

        # Replace <!-- IMG:N --> markers with actual base64 img tags (sequential over valid images)
        for seq_i, (_orig_i, img) in enumerate(valid_images, start=1):
            marker = f"<!-- IMG:{seq_i} -->"
            if marker in html:
                html = html.replace(marker, _build_image_tag(img, seq_i))

        if featured_index is not None and not featured_used_by_llm:
            # Enforcement, not just a prompt request: Claude ignored the mandatory
            # marker, so force-place the image ourselves. Both branches insert it as
            # a normal-flow block (never position:absolute), which is what makes the
            # "no overlap" guarantee hold even when the LLM didn't cooperate.
            log.error(
                "HTML gen: Claude omitted the mandatory featured-image marker "
                "<!-- IMG:%d --> — using fallback placement", featured_index,
            )
            fallback_tag = _build_image_tag(featured_img_data, featured_index)
            if is_custom:
                fallback_block = f'<div style="max-width:900px;margin:2rem auto;">{fallback_tag}</div>'
                html = re.sub(r'<body\b[^>]*>', lambda m: m.group(0) + fallback_block, html, count=1)
            else:
                # Marker path: html here is still raw SECTION-marker text (chrome
                # assembly happens later in core/cim_assembler.py) — drop the image
                # into the first section's normal content flow, right after its
                # opening marker, rather than trying to inject into a <body> that
                # doesn't exist yet at this stage.
                first_section_open = re.search(r'<!--\s*SECTION\b[^>]*-->', html)
                if first_section_open:
                    insert_at = first_section_open.end()
                    html = html[:insert_at] + fallback_tag + html[insert_at:]
                # else: no SECTION markers at all — already a contract violation that
                # core.cim_assembler.assemble() falls back on separately; nothing
                # more can be safely done with unstructured text here.

        return html
    except Exception as exc:
        log.error("HTML generation failed: %s", exc)
        raise

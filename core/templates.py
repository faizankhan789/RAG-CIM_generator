"""CIM v2 design templates — same 10-section content, different visual design.

Each template overrides fonts, color palette, structural/layout tendencies, and
section heading labels used in the final HTML generation prompt. Content
extraction (_EXTRACTION_PROMPT in llm.py) is unaffected — templates only steer
the HTML formatter step.
"""

from __future__ import annotations

from typing import Any

# Standard section headings (as they appear in _HTML_PROMPT / _EXTRACTION_PROMPT).
_STANDARD_HEADINGS = [
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

TEMPLATES: dict[str, dict[str, Any]] = {
    "classic": {
        "id": "classic",
        "name": "Classic Executive",
        "tagline": "Traditional investment-bank style — navy/gold, serif headers.",
        "allow_brand_override": True,
        "headings": None,   # None = use standard headings, no palette/font override
        "palette": None,
        "fonts": None,
        "layout_notes": None,
    },
    "minimalist": {
        "id": "minimalist",
        "name": "Modern Minimalist",
        "tagline": "Clean monochrome + single accent, generous whitespace.",
        "allow_brand_override": False,
        "palette": {
            "primary": "#141414", "accent": "#3a6ea5",
            "light": "#fafafa", "mid": "#5c5c5c",
        },
        "fonts": {
            "heading": "'Helvetica Neue', Helvetica, Arial, sans-serif",
            "body": "'Helvetica Neue', Helvetica, Arial, sans-serif",
        },
        "layout_notes": (
            "Strict minimalism: NO gradients, NO decorative shapes, NO drop shadows on cards. "
            "Flat backgrounds only. Use thin 1px hairline rules (not thick bars) to separate sections. "
            "Generous whitespace — increase section padding by ~50% vs a typical CIM. "
            "Color is used sparingly: only for the accent underline beneath headings, small "
            "eyebrow labels, and one highlight box per section — everything else stays black/white/grey. "
            "Typography carries the hierarchy: use weight and size contrast rather than color or ornament. "
            "Charts: flat single-accent-color fills only, never a gradient — bar fills in accent, donut "
            "arcs differentiated by accent/grey/black rather than multiple hues. Icons: 1px hairline "
            "stroke-width, used only in the stat-strip and Executive Summary checks — leave the section "
            "footer as plain text, no lock icon."
        ),
        "cover_override": (
            "REPLACE the 'PAGE 1 — COVER' spec above with this — the defining trait of this template is "
            "LEFT-ALIGNMENT, not centering. Flat solid primary-color background — no gradient, no decorative "
            "circles/bands, no image overlay. ALL cover content is left-aligned (text-align:left, "
            "align-items:flex-start), sitting in the lower-left area of the page rather than dead-center. "
            "Top bar unchanged (logo/empty left, CIM label right) but replace the accent gradient top border "
            "with a plain 1px hairline. Industry label: plain uppercase small text in accent color — NO pill "
            "shape, no background fill, no border-radius. Business name: massive (4.5rem) bold white, "
            "left-aligned, max 2 lines. A short (60px) thin 1px accent rule below the name, left-aligned not "
            "centered. Tagline left-aligned, muted grey, below the rule. Asking price: plain left-aligned "
            "text — small 'ASKING PRICE' label above a bold white value — NEVER a filled chip/box/border. "
            "Bottom bar: thin 1px top hairline (not a translucent band), same 3-part content left-aligned overall."
        ),
        "section_header_override": (
            "REPLACE the 'SECTION HEADER' spec above: no gradient band. Plain white/light background with a "
            "single 1px bottom hairline border in primary color. Roman numeral (small, grey, monospace) and "
            "section title (bold black) sit inline on ONE line, separated by a small gap — never stacked, "
            "never on a colored band, no decorative right-side accent element."
        ),
        "headings": {
            "I. Executive Summary": "Snapshot",
            "II. Company Overview": "The Business",
            "III. Financial Information": "The Numbers",
            "IV. Operations": "How It Runs",
            "V. Marketing and Sales": "Go to Market",
            "VI. Legal and Regulatory": "Compliance",
            "VII. Human Resources": "The Team",
            "VIII. Growth Opportunities": "What's Next",
            "IX. Risks": "Risk Factors",
            "X. Appendix": "Appendix",
        },
    },
    "editorial": {
        "id": "editorial",
        "name": "Bold Editorial",
        "tagline": "Magazine-style layout — big pull quotes, asymmetric columns.",
        "allow_brand_override": False,
        "palette": {
            "primary": "#1a1a1a", "accent": "#c94f3d",
            "light": "#f7f3ec", "mid": "#4a4a4a",
        },
        "fonts": {
            "heading": "Georgia, 'Times New Roman', Times, serif",
            "body": "'Trebuchet MS', 'Lucida Grande', Tahoma, sans-serif",
        },
        "layout_notes": (
            "Editorial magazine feel: use large serif display headlines (3rem+) for section titles, "
            "set in an asymmetric grid (e.g. a wide 65% column of narrative text beside a narrow 35% "
            "sidebar of pull quotes, stats, or captions — not a symmetric two-column grid). "
            "Open each major section with a large pull-quote or stat rendered in oversized italic serif type "
            "with a thick 6px accent-colored left border. "
            "Use thick horizontal accent-colored rule dividers (4-6px) between major blocks, not thin lines. "
            "Warm off-white page background throughout, near-black text. "
            "Charts: styled like newspaper data-journalism graphics — thin 1px strokes, serif value/period "
            "labels, muted ink tones, no gradients or drop shadows. Icons: simple restrained line-art matching "
            "the masthead's editorial tone, never bright or playful."
        ),
        "cover_override": (
            "REPLACE the 'PAGE 1 — COVER' spec above with a newspaper-masthead layout — the defining trait "
            "is a LIGHT background and LEFT-aligned asymmetry, never the dark centered layout described above. "
            "Warm off-white background (use the light color), not a dark gradient. Top bar unchanged (logo "
            "left / CIM label right) but add a thick 6px accent-colored rule beneath it, like a masthead line. "
            "Business name renders as an oversized (5.5rem+) serif masthead headline in near-black, "
            "LEFT-aligned, up to 90% width, may wrap 2 lines. Italic tagline left-aligned directly below it. "
            "Beside or below this headline, place an asymmetric accent-colored sidebar box (~30-35% width, "
            "white text) holding the industry label (plain text, NOT a rounded pill) and the asking price — "
            "positioned off to one side, never centered under the headline. No radial/diagonal decorative "
            "shapes behind the text — one thick horizontal accent rule under the whole masthead block instead. "
            "Bottom bar: same 3-part content but left-aligned, sitting directly on the light background "
            "(not a dark translucent band)."
        ),
        "section_header_override": (
            "REPLACE the 'SECTION HEADER' spec above: no dark gradient band — plain light background matching "
            "the page body. Section title in large (2.5rem+) serif display type, left-aligned. Render the "
            "Roman numeral as a large decorative outline/ghost numeral (low-opacity accent color, oversized, "
            "positioned behind or beside the title) rather than a small label above it. A thick 4-6px accent "
            "rule runs directly under the title in place of any colored band."
        ),
        "headings": {
            "I. Executive Summary": "The Opportunity",
            "II. Company Overview": "Inside the Business",
            "III. Financial Information": "By the Numbers",
            "IV. Operations": "How It Operates",
            "V. Marketing and Sales": "Reaching the Market",
            "VI. Legal and Regulatory": "Legal Standing",
            "VII. Human Resources": "The People",
            "VIII. Growth Opportunities": "Where It's Headed",
            "IX. Risks": "What Could Go Wrong",
            "X. Appendix": "Further Reading",
        },
    },
    "luxury": {
        "id": "luxury",
        "name": "Luxury Estate",
        "tagline": "Dark, elegant, bronze/gold accents — refined and restrained.",
        "allow_brand_override": False,
        "palette": {
            "primary": "#0d0d0d", "accent": "#b8974a",
            "light": "#f5f0e6", "mid": "#2a2118",
        },
        "fonts": {
            "heading": "'Bodoni MT', Didot, Georgia, serif",
            "body": "Palatino, 'Palatino Linotype', Georgia, serif",
        },
        "layout_notes": (
            "Refined, restrained luxury feel — never bright or busy. Section labels in small-caps with "
            "wide letter-spacing (0.15em+). Thin ornamental gold dividers (a short centered accent rule, "
            "not a full-width bar) between subsections. Generous margins (padding 4rem+ on section bodies). "
            "No bright saturated colors anywhere — palette stays within primary/accent/light/mid. "
            "No card drop-shadows heavier than a subtle 1px border; avoid playful rounded corners — use "
            "sharp or minimally rounded (2-4px) corners throughout. "
            "Charts: single muted gold-tone strokes/fills only, never a multi-hue palette — differentiate "
            "donut segments by opacity (100% / 60% / 35% of the gold accent) rather than by hue. Icons: thin "
            "1px gold stroke, used only in the stat-strip and the section-footer lock mark — never as bullet "
            "markers (keep bullets as plain gold dots, per the restraint rule above)."
        ),
        "cover_override": (
            "MODIFY the 'PAGE 1 — COVER' spec above (this template keeps the centered layout, but changes the "
            "ornamentation — do not reuse the pill badge or filled price chip). Remove the rounded industry "
            "badge pill entirely — replace with a small-caps label flanked by thin horizontal gold hairlines "
            "on each side (e.g. '── HOTEL & HOSPITALITY ──'), no background fill, no border-radius. Add a thin "
            "double-line gold border frame inset ~24px from the cover's outer edge on the top/left/right sides, "
            "running the perimeter (like an invitation/certificate border), with small ornamental gold corner "
            "brackets (simple CSS borders, ~24px) at all four corners just inside the frame. The frame and "
            "corner brackets are decorative background elements — draw them BEFORE the top bar in the HTML and "
            "give them a LOWER z-index than the top bar's z-index:5 (see the base 'PAGE 1 — COVER' top-bar "
            "spec), so they always render behind, never crossing over, the logo/CIM-label row. The SAME "
            "clearance is required at the bottom: the bottom bar (see base spec) is a translucent band whose "
            "rendered height (padding + line-height, typically 50-60px) is taller than the 24px top-corner "
            "inset, so a matching 24px inset at the bottom WILL sit inside the bottom bar's footprint and "
            "collide with it — do not reuse the same 24px value for the frame's bottom edge or the two bottom "
            "corner brackets. Instead, size the frame's bottom inset (and the two bottom corner brackets' "
            "position) to sit ABOVE the bottom bar's actual rendered height, so the frame's bottom line and "
            "both bottom corner brackets clear it with the same margin the top corners keep from the top bar. "
            "Business name: gold-tinted white, "
            "small-caps, wide letter-spacing (0.1em+), centered, serif, more restrained in size (3.5rem, not "
            "4.5rem) than other templates. Asking price: NO filled chip/box — plain centered text flanked by a "
            "thin gold rule above and below, matching the eyebrow-label style."
        ),
        "section_header_override": (
            "MODIFY the 'SECTION HEADER' spec above: keep the dark band, but center everything instead of "
            "left-aligning — numeral and title stacked and centered, title in small-caps, with a thin gold "
            "rule directly above and below the title in place of a right-side decorative accent bar."
        ),
        "headings": {
            "I. Executive Summary": "Investment Overview",
            "II. Company Overview": "The Enterprise",
            "III. Financial Information": "Financial Performance",
            "IV. Operations": "Operations & Management",
            "V. Marketing and Sales": "Market Position",
            "VI. Legal and Regulatory": "Legal & Compliance",
            "VII. Human Resources": "Leadership & Staff",
            "VIII. Growth Opportunities": "Growth Potential",
            "IX. Risks": "Risk Considerations",
            "X. Appendix": "Supporting Documents",
        },
    },
    "startup": {
        "id": "startup",
        "name": "Tech Growth",
        "tagline": "Vibrant dashboard style — bold sans, gradient stat cards.",
        "allow_brand_override": False,
        "palette": {
            "primary": "#0b1020", "accent": "#7c5cff",
            "light": "#f5f6ff", "mid": "#1e2140",
        },
        "fonts": {
            "heading": "'Century Gothic', Futura, 'Trebuchet MS', sans-serif",
            "body": "'Segoe UI', Arial, sans-serif",
        },
        "layout_notes": (
            "Modern tech/startup dashboard energy: bold geometric sans headings, punchy and confident tone. "
            "Stat strips and highlight boxes use a gradient background from accent (#7c5cff) to a teal "
            "(#22d3ee) rather than a flat color fill. "
            "Cards throughout use rounded corners (16px) and soft colored shadows (not grey) tinted toward "
            "the accent color. "
            "Favor dense card-grid layouts (card-grid-2 / card-grid-3) over long narrative paragraphs where "
            "the data supports it — this template is stat-forward and dashboard-like. "
            "Charts: gradient fills (accent→teal, via <linearGradient> in <defs>) on bars and the primary "
            "donut segment, rounded bar corners (rx 6-8px), soft accent-tinted glow — matches the glass-"
            "morphism stat cards. Icons: bolder 2px stroke-width, each sitting inside a small filled circular "
            "accent-tinted badge rather than bare on the background."
        ),
        "cover_override": (
            "REPLACE the 'PAGE 1 — COVER' spec above with a two-column dashboard layout — the defining trait "
            "is a SPLIT layout, never the single centered column described above. The top bar itself is "
            "unchanged from the base spec (full page width, logo left / CIM label right, z-index:5) and sits "
            "ABOVE the two-column split, not nested inside either column — do not shrink it to the left "
            "column's width or let the glass stat cards render above it. Below the top bar, split into two "
            "columns. Left column (~55% width): business name left-aligned in bold geometric sans (4rem), tagline below, "
            "and the asking price rendered as a rounded gradient pill-button (accent→teal gradient, white bold "
            "text) — all left-aligned and vertically centered within the column. Right column (~45% width): a "
            "decorative 2×2 grid of small glass-morphism stat-preview cards (rounded 16px corners, ~5% white "
            "fill, soft accent-tinted glow shadow), each showing one real metric label + value pulled from the "
            "business's actual key stats (occupancy, revenue growth, etc. — only real data, never invented). "
            "Background: deep navy solid with a soft radial accent-to-teal glow localized behind the right "
            "column only (not full-page). Bottom bar: same 3-part content, left-aligned to match the two-column "
            "layout."
        ),
        "section_header_override": (
            "REPLACE the 'SECTION HEADER' spec above: instead of a full-width band, use a floating rounded-"
            "corner (16px) gradient card (accent→teal) inset with side margins (not edge-to-edge), with a soft "
            "accent-tinted drop shadow. Roman numeral and section title sit together on one line inside the card."
        ),
        "headings": {
            "I. Executive Summary": "TL;DR",
            "II. Company Overview": "What We Do",
            "III. Financial Information": "The Numbers",
            "IV. Operations": "How It Works",
            "V. Marketing and Sales": "Go-To-Market",
            "VI. Legal and Regulatory": "Compliance",
            "VII. Human Resources": "The Team",
            "VIII. Growth Opportunities": "What's Next",
            "IX. Risks": "Risks & Mitigations",
            "X. Appendix": "Appendix",
        },
    },
}

DEFAULT_TEMPLATE_ID = "classic"


def get_template(template_id: str | None) -> dict[str, Any]:
    """Look up a template by id, falling back to the classic default."""
    return TEMPLATES.get((template_id or "").strip().lower(), TEMPLATES[DEFAULT_TEMPLATE_ID])


def list_templates() -> list[dict[str, Any]]:
    """Metadata for the CRM template-picker UI (id, name, tagline, palette swatch)."""
    return [
        {
            "id": t["id"],
            "name": t["name"],
            "tagline": t["tagline"],
            "palette": t["palette"],
        }
        for t in TEMPLATES.values()
    ]

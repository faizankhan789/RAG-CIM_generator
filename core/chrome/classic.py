"""Chrome skeleton for the 'classic' (Classic Executive) template.

core/templates.py's "classic" entry has palette=fonts=cover_override=
section_header_override=None — meaning it uses the BASE spec's default cover/
TOC/section-header design (core/llm.py "PAGE 1 — COVER" / "SECTION HEADER"),
not a template-specific override. Its color palette is chosen per-listing from
the industry fallback table (core/llm.py STEP 2) rather than being fixed — the
lookup below is that same table, made deterministic instead of an LLM
re-derivation, keyed on the <!-- INDUSTRY: ... --> marker the LLM still emits
(industry classification itself stays a genuine LLM judgment call; only the
palette-per-industry mapping is now a fixed table lookup).
"""

from __future__ import annotations

from core.chrome._shared import base_css, disclaimer_page, esc, section_footer, wrap_document

FONTS = {
    "heading": "'Segoe UI', 'Helvetica Neue', Arial, sans-serif",
    "body": "'Segoe UI', 'Helvetica Neue', Arial, sans-serif",
}

# Fallback industry palettes (core/llm.py STEP 2) — used only when no brand
# colors were supplied; matched by substring against the LLM's <!-- INDUSTRY -->
# marker text.
_INDUSTRY_PALETTES: list[tuple[str, dict]] = [
    ("hotel", {"primary": "#1a0a0e", "accent": "#b8902a", "light": "#fdf6ec", "mid": "#6b1f2a"}),
    ("hospitality", {"primary": "#1a0a0e", "accent": "#b8902a", "light": "#fdf6ec", "mid": "#6b1f2a"}),
    ("restaurant", {"primary": "#1c0f0a", "accent": "#c9622a", "light": "#fef9f5", "mid": "#7a3520"}),
    ("food", {"primary": "#1c0f0a", "accent": "#c9622a", "light": "#fef9f5", "mid": "#7a3520"}),
    ("travel", {"primary": "#021e35", "accent": "#e6a817", "light": "#f0f8ff", "mid": "#0d4f72"}),
    ("tourism", {"primary": "#021e35", "accent": "#e6a817", "light": "#f0f8ff", "mid": "#0d4f72"}),
    ("health", {"primary": "#041f1f", "accent": "#1fa37a", "light": "#f5fffd", "mid": "#1a6b6b"}),
    ("tech", {"primary": "#07080f", "accent": "#6366f1", "light": "#f5f6ff", "mid": "#0f172a"}),
    ("saas", {"primary": "#07080f", "accent": "#6366f1", "light": "#f5f6ff", "mid": "#0f172a"}),
    ("retail", {"primary": "#180d06", "accent": "#e05c2a", "light": "#fff8f5", "mid": "#2d3748"}),
    ("real estate", {"primary": "#0a1a0f", "accent": "#c9973a", "light": "#f8fbf4", "mid": "#1a4731"}),
    ("manufactur", {"primary": "#0d1117", "accent": "#e67e22", "light": "#fafafa", "mid": "#2c3e50"}),
    ("education", {"primary": "#0a1229", "accent": "#f59e0b", "light": "#fffdf0", "mid": "#1e3a8a"}),
    ("professional", {"primary": "#0f1117", "accent": "#64748b", "light": "#f8fafc", "mid": "#1f2937"}),
]
_DEFAULT_PALETTE = {"primary": "#101418", "accent": "#c9a227", "light": "#f7f7f5", "mid": "#2a2a2a"}


def palette_for_industry(industry: str, brand_primary: str = "", brand_accent: str = "") -> dict:
    """Deterministic replacement for the LLM's own STEP 2 palette pick."""
    if brand_primary and brand_accent:
        return {"primary": brand_primary, "accent": brand_accent, "light": "#fafafa", "mid": brand_primary}
    lowered = (industry or "").lower()
    for needle, palette in _INDUSTRY_PALETTES:
        if needle in lowered:
            return palette
    return _DEFAULT_PALETTE


def _cover(business_name: str, asking_price: str, industry: str, date_str: str, logo_html: str, palette: dict) -> str:
    return f"""
    <div id="cover" style="min-height:100vh;position:relative;overflow:hidden;
        background:linear-gradient(135deg,{palette['primary']} 0%,{palette['mid']} 100%);
        display:flex;flex-direction:column;">
        <div style="position:absolute;top:-120px;right:-120px;width:420px;height:420px;border-radius:50%;
            background:{palette['accent']};opacity:0.10;"></div>
        <div style="position:absolute;bottom:-160px;left:-100px;width:380px;height:380px;border-radius:50%;
            background:{palette['accent']};opacity:0.08;"></div>
        <div style="display:flex;justify-content:space-between;align-items:center;width:100%;
            padding:2rem 3rem;position:relative;z-index:5;border-top:3px solid {palette['accent']};">
            <div>{logo_html}</div>
            <div style="text-align:right;color:{palette['accent']};font-size:0.75rem;
                letter-spacing:0.15em;text-transform:uppercase;">CONFIDENTIAL INFORMATION MEMORANDUM</div>
        </div>
        <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;
            text-align:center;padding:2rem 3rem;position:relative;z-index:2;">
            <div style="background:{palette['accent']};color:#fff;padding:0.5rem 1.5rem;border-radius:999px;
                text-transform:uppercase;letter-spacing:0.12em;font-size:0.8rem;margin:0 auto 2rem;">
                {esc(industry)}</div>
            <h1 style="color:#fff;font-size:4.5rem;line-height:1.1;font-weight:700;max-width:900px;
                margin:0 auto;">{esc(business_name)}</h1>
            <div style="width:80px;height:2px;background:{palette['accent']};margin:2rem auto;"></div>
            <div style="background:{palette['accent']};border-radius:12px;padding:1.5rem 2.5rem;
                display:inline-block;margin-top:1rem;">
                <div style="color:rgba(255,255,255,0.85);font-size:0.85rem;letter-spacing:0.1em;
                    text-transform:uppercase;">Asking Price</div>
                <div style="color:#fff;font-size:2.5rem;font-weight:700;">{esc(asking_price)}</div>
            </div>
        </div>
        <div style="position:relative;z-index:2;background:rgba(0,0,0,0.3);width:100%;padding:1rem 3rem;
            display:flex;justify-content:space-between;align-items:center;border-top:1px solid {palette['accent']};
            flex-wrap:wrap;gap:0.5rem;">
            <span style="font-size:0.75rem;color:rgba(255,255,255,0.85);font-style:italic;">
                Prepared exclusively for prospective acquirers</span>
            <span style="font-size:0.75rem;color:rgba(255,255,255,0.85);">{esc(date_str)}</span>
            <span style="font-size:0.75rem;color:rgba(255,255,255,0.85);text-transform:uppercase;
                letter-spacing:0.1em;">Strictly Private &amp; Confidential</span>
        </div>
    </div>"""


def _toc(sections: list[dict], palette: dict) -> str:
    rows = "\n".join(
        f"""<div style="display:flex;align-items:center;gap:1rem;padding:0.9rem 0;
            border-bottom:1px solid {palette['light']};">
            <span style="color:{palette['accent']};font-weight:700;font-family:monospace;min-width:3rem;">
                {esc(s['num'])}</span>
            <span style="color:{palette['primary']};font-weight:500;">{esc(s['title'])}</span>
            <span style="flex-grow:1;border-bottom:1px dotted #ccc;margin:0 0.5rem;height:0.6em;"></span>
            <span style="color:{palette['accent']};">&#8594;</span>
        </div>"""
        for s in sections
    )
    return f"""
    <div id="toc" style="min-height:700px;background:#fff;padding:4rem 3rem;
        border-left:4px solid {palette['accent']};">
        <h2 style="color:{palette['primary']};font-size:2rem;font-weight:700;margin-bottom:2rem;">
            Table of Contents</h2>
        {rows}
    </div>"""


def render_section(num: str, title: str, body_html: str, business_name: str, palette: dict) -> str:
    return f"""
    <div id="section-{esc(num)}" style="background:#fff;">
        <div style="background:linear-gradient(135deg,{palette['primary']} 0%,{palette['mid']} 100%);
            padding:2.5rem 3rem;position:relative;">
            <div style="color:{palette['accent']};font-size:0.85rem;letter-spacing:0.12em;
                text-transform:uppercase;margin-bottom:0.4rem;">{esc(num)}</div>
            <h2 style="color:#fff;font-size:2rem;font-weight:700;">{esc(title)}</h2>
            <div style="position:absolute;top:0;right:0;bottom:0;width:6px;background:{palette['accent']};"></div>
        </div>
        <div style="background:#fff;padding:3rem;">{body_html}</div>
        {section_footer(business_name, num, title, palette)}
    </div>"""


def render(business_name: str, asking_price: str, industry: str, date_str: str, logo_html: str,
           stats_html: str, sections: list[dict], *,
           brand_primary: str = "", brand_accent: str = "") -> str:
    palette = palette_for_industry(industry, brand_primary, brand_accent)
    body = (
        _cover(business_name, asking_price, industry, date_str, logo_html, palette)
        + _toc(sections, palette)
        + (stats_html or "")
        + "".join(
            render_section(s["num"], s["title"], s["body_html"], business_name, palette)
            for s in sections
        )
        + disclaimer_page(business_name, palette)
    )
    return wrap_document(base_css(FONTS), body)

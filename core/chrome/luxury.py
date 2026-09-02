"""Chrome skeleton for the 'luxury' (Luxury Estate) template.

Bakes in both fixes discovered this session for the ornamental gold frame:
1. Frame/corner-bracket z-index (1) stays below the top bar's z-index (5) —
   the top-clearance fix.
2. The frame's BOTTOM edge and bottom corner brackets use a 64px inset
   (not 24px) — the bottom bar's translucent band is ~50-60px tall, so a
   symmetric 24px inset would sit inside its footprint and collide with it.
   64px clears it with the same margin the top corners keep from the top bar.
Because this is now a literal, frozen skeleton instead of an LLM re-deriving
the CSS from prose each generation, this bug class cannot regress.
"""

from __future__ import annotations

from core.templates import TEMPLATES
from core.chrome._shared import base_css, disclaimer_page, esc, section_footer, wrap_document

PALETTE = TEMPLATES["luxury"]["palette"]
FONTS = TEMPLATES["luxury"]["fonts"]

_TOP_INSET = 24
_BOTTOM_INSET = 64  # taller than the ~50-60px rendered bottom-bar height


def _cover(business_name: str, asking_price: str, industry: str, date_str: str, logo_html: str) -> str:
    p, f = PALETTE, FONTS
    return f"""
    <div id="cover" style="min-height:100vh;position:relative;overflow:hidden;
        background:linear-gradient(135deg,#0d0d0d 0%,{p['mid']} 100%);
        display:flex;flex-direction:column;">
        <div style="position:absolute;top:{_TOP_INSET}px;right:{_TOP_INSET}px;bottom:{_BOTTOM_INSET}px;
            left:{_TOP_INSET}px;border:2px double {p['accent']};pointer-events:none;z-index:1;"></div>
        <div style="position:absolute;top:{_TOP_INSET}px;left:{_TOP_INSET}px;width:24px;height:24px;
            border-top:2px solid {p['accent']};border-left:2px solid {p['accent']};z-index:1;"></div>
        <div style="position:absolute;top:{_TOP_INSET}px;right:{_TOP_INSET}px;width:24px;height:24px;
            border-top:2px solid {p['accent']};border-right:2px solid {p['accent']};z-index:1;"></div>
        <div style="position:absolute;bottom:{_BOTTOM_INSET}px;right:{_TOP_INSET}px;width:24px;height:24px;
            border-bottom:2px solid {p['accent']};border-right:2px solid {p['accent']};z-index:1;"></div>
        <div style="position:absolute;bottom:{_BOTTOM_INSET}px;left:{_TOP_INSET}px;width:24px;height:24px;
            border-bottom:2px solid {p['accent']};border-left:2px solid {p['accent']};z-index:1;"></div>

        <div style="display:flex;justify-content:space-between;align-items:center;width:100%;
            padding:2rem 3rem;position:relative;z-index:5;border-bottom:1px solid {p['accent']};">
            <div>{logo_html}</div>
            <div style="text-align:right;color:{p['accent']};font-size:0.75rem;
                letter-spacing:0.15em;text-transform:uppercase;">CONFIDENTIAL INFORMATION MEMORANDUM</div>
        </div>

        <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;
            text-align:center;padding:2rem 3rem;position:relative;z-index:2;">
            <div style="color:{p['accent']};text-transform:uppercase;letter-spacing:0.2em;
                font-size:0.8rem;">&mdash;&mdash; {esc(industry)} &mdash;&mdash;</div>
            <h1 style="font-family:{f['heading']};color:#f2e6c9;text-transform:uppercase;
                letter-spacing:0.1em;font-size:3.5rem;font-weight:700;max-width:800px;margin:2rem auto;">
                {esc(business_name)}</h1>
            <div style="width:60px;height:1px;background:{p['accent']};margin:0.5rem auto;"></div>
            <div style="color:{p['accent']};font-size:0.8rem;">Asking Price</div>
            <div style="color:#fff;font-size:2rem;font-weight:700;margin:0.3rem 0;">{esc(asking_price)}</div>
            <div style="width:60px;height:1px;background:{p['accent']};margin:0.5rem auto;"></div>
        </div>

        <div style="position:relative;z-index:2;background:rgba(0,0,0,0.3);width:100%;padding:1rem 3rem;
            display:flex;justify-content:space-between;align-items:center;border-top:1px solid {p['accent']};
            flex-wrap:wrap;gap:0.5rem;">
            <span style="font-size:0.75rem;color:rgba(255,255,255,0.85);font-style:italic;">
                Prepared exclusively for prospective acquirers</span>
            <span style="font-size:0.75rem;color:rgba(255,255,255,0.85);">{esc(date_str)}</span>
            <span style="font-size:0.75rem;color:rgba(255,255,255,0.85);text-transform:uppercase;
                letter-spacing:0.1em;">Strictly Private &amp; Confidential</span>
        </div>
    </div>"""


def _toc(sections: list[dict]) -> str:
    p, f = PALETTE, FONTS
    rows = "\n".join(
        f"""<div style="display:flex;align-items:center;gap:1rem;padding:0.9rem 0;
            border-bottom:1px solid #e8dfc8;">
            <span style="color:{p['accent']};font-weight:700;font-family:monospace;min-width:3rem;">
                {esc(s['num'])}</span>
            <span style="font-family:{f['heading']};color:{p['primary']};font-weight:500;">
                {esc(s['title'])}</span>
            <span style="flex-grow:1;border-bottom:1px dotted #c9b98a;margin:0 0.5rem;height:0.6em;"></span>
            <span style="color:{p['accent']};">&#8594;</span>
        </div>"""
        for s in sections
    )
    return f"""
    <div id="toc" style="min-height:700px;background:{p['light']};padding:4rem 3rem;">
        <h2 style="font-family:{f['heading']};color:{p['primary']};font-size:2rem;font-weight:700;
            margin-bottom:2rem;text-align:center;">Table of Contents</h2>
        {rows}
    </div>"""


def render_section(num: str, title: str, body_html: str, business_name: str) -> str:
    p, f = PALETTE, FONTS
    return f"""
    <div id="section-{esc(num)}" style="background:#fff;">
        <div style="background:linear-gradient(135deg,#0d0d0d 0%,{p['mid']} 100%);
            padding:2.5rem 3rem;text-align:center;">
            <div style="color:{p['accent']};font-size:0.85rem;letter-spacing:0.12em;
                text-transform:uppercase;margin-bottom:0.5rem;">{esc(num)}</div>
            <div style="width:50px;height:1px;background:{p['accent']};margin:0 auto 0.8rem;"></div>
            <h2 style="font-family:{f['heading']};color:#f2e6c9;text-transform:uppercase;
                letter-spacing:0.08em;font-size:1.6rem;font-weight:700;">{esc(title)}</h2>
            <div style="width:50px;height:1px;background:{p['accent']};margin:0.8rem auto 0;"></div>
        </div>
        <div style="background:#fff;padding:3rem;">{body_html}</div>
        {section_footer(business_name, num, title, p)}
    </div>"""


def render(business_name: str, asking_price: str, industry: str, date_str: str, logo_html: str,
           stats_html: str, sections: list[dict], **_ignored) -> str:
    body = (
        _cover(business_name, asking_price, industry, date_str, logo_html)
        + _toc(sections)
        + (stats_html or "")
        + "".join(render_section(s["num"], s["title"], s["body_html"], business_name) for s in sections)
        + disclaimer_page(business_name, PALETTE)
    )
    return wrap_document(base_css(FONTS), body)

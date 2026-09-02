"""Chrome skeleton for the 'startup' (Tech Growth, id="startup") template.

Note: the original prose spec's cover called for a right-column grid of real
metric preview cards (occupancy/revenue-growth/etc.). That data isn't part of
this module's inputs (business_name/asking_price/industry/date/logo/stats_html
/sections) — pulling it in would mean coupling frozen chrome to LLM-extracted
content, which is exactly what this rework is meant to avoid (see spec's
stat-strip-stays-LLM-authored decision). The two-column split, gradient glow,
and pill-button price are kept; the right column uses a decorative gradient
glow instead of fabricated/duplicated metric cards.
"""

from __future__ import annotations

from core.templates import TEMPLATES
from core.chrome._shared import base_css, disclaimer_page, esc, section_footer, wrap_document

PALETTE = TEMPLATES["startup"]["palette"]
FONTS = TEMPLATES["startup"]["fonts"]
_TEAL = "#22d3ee"


def _cover(business_name: str, asking_price: str, industry: str, date_str: str, logo_html: str) -> str:
    p, f = PALETTE, FONTS
    return f"""
    <div id="cover" style="min-height:100vh;position:relative;overflow:hidden;background:{p['primary']};
        display:flex;flex-direction:column;">
        <div style="position:absolute;top:10%;right:-10%;width:60%;height:60%;border-radius:50%;
            background:radial-gradient(circle,{p['accent']} 0%,{_TEAL} 50%,transparent 70%);opacity:0.18;"></div>
        <div style="display:flex;justify-content:space-between;align-items:center;width:100%;
            padding:2rem 3rem;position:relative;z-index:5;">
            <div>{logo_html}</div>
            <div style="text-align:right;color:{p['accent']};font-size:0.75rem;
                letter-spacing:0.15em;text-transform:uppercase;">CONFIDENTIAL INFORMATION MEMORANDUM</div>
        </div>
        <div style="flex:1;display:flex;position:relative;z-index:2;">
            <div style="width:55%;display:flex;flex-direction:column;justify-content:center;
                padding:2rem 2rem 2rem 4rem;">
                <div style="color:{p['accent']};text-transform:uppercase;letter-spacing:0.15em;
                    font-size:0.8rem;margin-bottom:1rem;">{esc(industry)}</div>
                <h1 style="font-family:{f['heading']};color:#fff;font-size:4rem;line-height:1.1;
                    font-weight:700;">{esc(business_name)}</h1>
                <div style="margin-top:2rem;display:inline-block;background:linear-gradient(135deg,
                    {p['accent']} 0%,{_TEAL} 100%);border-radius:999px;padding:1rem 2rem;width:fit-content;">
                    <div style="color:rgba(255,255,255,0.85);font-size:0.75rem;text-transform:uppercase;
                        letter-spacing:0.1em;">Asking Price</div>
                    <div style="color:#fff;font-size:1.8rem;font-weight:700;">{esc(asking_price)}</div>
                </div>
            </div>
            <div style="width:45%;"></div>
        </div>
        <div style="position:relative;z-index:2;width:100%;padding:1rem 3rem;
            display:flex;justify-content:space-between;align-items:center;border-top:1px solid {p['mid']};
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
            border-bottom:1px solid #eceafd;">
            <span style="color:{p['accent']};font-weight:700;font-family:monospace;min-width:3rem;">
                {esc(s['num'])}</span>
            <span style="font-family:{f['heading']};color:{p['primary']};font-weight:500;">
                {esc(s['title'])}</span>
            <span style="flex-grow:1;border-bottom:1px dotted #c9c6e8;margin:0 0.5rem;height:0.6em;"></span>
            <span style="color:{p['accent']};">&#8594;</span>
        </div>"""
        for s in sections
    )
    return f"""
    <div id="toc" style="min-height:700px;background:{p['light']};padding:4rem 3rem;">
        <h2 style="font-family:{f['heading']};color:{p['primary']};font-size:2rem;font-weight:700;
            margin-bottom:2rem;">Table of Contents</h2>
        {rows}
    </div>"""


def render_section(num: str, title: str, body_html: str, business_name: str) -> str:
    p, f = PALETTE, FONTS
    return f"""
    <div id="section-{esc(num)}" style="background:{p['light']};padding:1.5rem 2rem;">
        <div style="background:linear-gradient(135deg,{p['accent']} 0%,{_TEAL} 100%);
            border-radius:16px;padding:1.5rem 2rem;display:flex;align-items:baseline;gap:1rem;
            box-shadow:0 8px 24px rgba(124,92,255,0.25);margin-bottom:1.5rem;">
            <span style="color:rgba(255,255,255,0.85);font-family:monospace;">{esc(num)}</span>
            <h2 style="font-family:{f['heading']};color:#fff;font-size:1.6rem;font-weight:700;">
                {esc(title)}</h2>
        </div>
        <div style="background:#fff;border-radius:16px;padding:2.5rem;">{body_html}</div>
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

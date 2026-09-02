"""Chrome skeleton for the 'minimalist' (Modern Minimalist) template.

Fixed palette/fonts (core/templates.py TEMPLATES["minimalist"]) — no
industry-based palette lookup needed here, unlike classic.py.
"""

from __future__ import annotations

from core.templates import TEMPLATES
from core.chrome._shared import base_css, disclaimer_page, esc, section_footer, wrap_document

PALETTE = TEMPLATES["minimalist"]["palette"]
FONTS = TEMPLATES["minimalist"]["fonts"]


def _cover(business_name: str, asking_price: str, industry: str, date_str: str, logo_html: str) -> str:
    p = PALETTE
    return f"""
    <div id="cover" style="min-height:100vh;position:relative;background:{p['primary']};
        display:flex;flex-direction:column;">
        <div style="display:flex;justify-content:space-between;align-items:center;width:100%;
            padding:2rem 3rem;position:relative;z-index:5;border-bottom:1px solid {p['accent']};">
            <div>{logo_html}</div>
            <div style="text-align:right;color:{p['accent']};font-size:0.75rem;
                letter-spacing:0.15em;text-transform:uppercase;">CONFIDENTIAL INFORMATION MEMORANDUM</div>
        </div>
        <div style="flex:1;display:flex;flex-direction:column;align-items:flex-start;justify-content:flex-end;
            text-align:left;padding:2rem 4rem 6rem;">
            <div style="color:{p['accent']};text-transform:uppercase;letter-spacing:0.15em;font-size:0.85rem;
                margin-bottom:1.5rem;">{esc(industry)}</div>
            <h1 style="color:#fff;font-size:4.5rem;line-height:1.1;font-weight:700;max-width:800px;">
                {esc(business_name)}</h1>
            <div style="width:60px;height:1px;background:{p['accent']};margin:1.5rem 0;"></div>
            <div style="color:{p['mid']};font-size:0.85rem;letter-spacing:0.1em;text-transform:uppercase;">
                Asking Price</div>
            <div style="color:#fff;font-size:2rem;font-weight:700;">{esc(asking_price)}</div>
        </div>
        <div style="position:relative;width:100%;padding:1rem 3rem;border-top:1px solid {p['mid']};
            display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem;">
            <span style="font-size:0.75rem;color:rgba(255,255,255,0.85);font-style:italic;">
                Prepared exclusively for prospective acquirers</span>
            <span style="font-size:0.75rem;color:rgba(255,255,255,0.85);">{esc(date_str)}</span>
            <span style="font-size:0.75rem;color:rgba(255,255,255,0.85);text-transform:uppercase;
                letter-spacing:0.1em;">Strictly Private &amp; Confidential</span>
        </div>
    </div>"""


def _toc(sections: list[dict]) -> str:
    p = PALETTE
    rows = "\n".join(
        f"""<div style="display:flex;align-items:center;gap:1rem;padding:0.9rem 0;
            border-bottom:1px solid #eee;">
            <span style="color:{p['mid']};font-family:monospace;min-width:3rem;">{esc(s['num'])}</span>
            <span style="color:{p['primary']};font-weight:500;">{esc(s['title'])}</span>
            <span style="flex-grow:1;border-bottom:1px dotted #ccc;margin:0 0.5rem;height:0.6em;"></span>
            <span style="color:{p['accent']};">&#8594;</span>
        </div>"""
        for s in sections
    )
    return f"""
    <div id="toc" style="min-height:700px;background:#fff;padding:4rem 3rem;">
        <h2 style="color:{p['primary']};font-size:2rem;font-weight:700;margin-bottom:2rem;">
            Table of Contents</h2>
        {rows}
    </div>"""


def render_section(num: str, title: str, body_html: str, business_name: str) -> str:
    p = PALETTE
    return f"""
    <div id="section-{esc(num)}" style="background:#fff;">
        <div style="background:#fff;padding:2rem 3rem;border-bottom:1px solid {p['primary']};
            display:flex;align-items:baseline;gap:1rem;">
            <span style="color:{p['mid']};font-family:monospace;font-size:0.9rem;">{esc(num)}</span>
            <h2 style="color:{p['primary']};font-size:1.6rem;font-weight:700;">{esc(title)}</h2>
        </div>
        <div style="background:#fff;padding:3rem;">{body_html}</div>
        {section_footer(business_name, num, title, p, lock_icon=False)}
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

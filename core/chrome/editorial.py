"""Chrome skeleton for the 'editorial' (Bold Editorial) template."""

from __future__ import annotations

from core.templates import TEMPLATES
from core.chrome._shared import base_css, disclaimer_page, esc, section_footer, wrap_document

PALETTE = TEMPLATES["editorial"]["palette"]
FONTS = TEMPLATES["editorial"]["fonts"]


def _cover(business_name: str, asking_price: str, industry: str, date_str: str, logo_html: str) -> str:
    p, f = PALETTE, FONTS
    return f"""
    <div id="cover" style="min-height:100vh;position:relative;background:{p['light']};
        display:flex;flex-direction:column;">
        <div style="display:flex;justify-content:space-between;align-items:center;width:100%;
            padding:2rem 3rem;position:relative;z-index:5;">
            <div>{logo_html}</div>
            <div style="text-align:right;color:{p['accent']};font-size:0.75rem;
                letter-spacing:0.15em;text-transform:uppercase;">CONFIDENTIAL INFORMATION MEMORANDUM</div>
        </div>
        <div style="height:6px;background:{p['accent']};width:100%;"></div>
        <div style="flex:1;display:flex;flex-direction:column;justify-content:center;padding:3rem 4rem;">
            <h1 style="font-family:{f['heading']};color:{p['primary']};font-size:5.5rem;line-height:1.05;
                font-weight:700;max-width:90%;">{esc(business_name)}</h1>
            <p style="font-family:{f['heading']};font-style:italic;color:{p['mid']};font-size:1.2rem;
                margin-top:1rem;">A Confidential Opportunity</p>
            <div style="width:100%;height:4px;background:{p['accent']};margin:2.5rem 0;"></div>
            <div style="display:flex;justify-content:flex-end;">
                <div style="background:{p['accent']};color:#fff;padding:1.5rem 2rem;width:34%;">
                    <div style="text-transform:uppercase;letter-spacing:0.1em;font-size:0.75rem;
                        opacity:0.9;">{esc(industry)}</div>
                    <div style="height:1px;background:rgba(255,255,255,0.4);margin:0.8rem 0;"></div>
                    <div style="font-size:0.8rem;text-transform:uppercase;letter-spacing:0.08em;
                        opacity:0.9;">Asking Price</div>
                    <div style="font-size:1.8rem;font-weight:700;">{esc(asking_price)}</div>
                </div>
            </div>
        </div>
        <div style="width:100%;padding:1rem 3rem;border-top:1px solid {p['mid']};
            display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem;">
            <span style="font-size:0.75rem;color:{p['mid']};font-style:italic;">
                Prepared exclusively for prospective acquirers</span>
            <span style="font-size:0.75rem;color:{p['mid']};">{esc(date_str)}</span>
            <span style="font-size:0.75rem;color:{p['mid']};text-transform:uppercase;
                letter-spacing:0.1em;">Strictly Private &amp; Confidential</span>
        </div>
    </div>"""


def _toc(sections: list[dict]) -> str:
    p, f = PALETTE, FONTS
    rows = "\n".join(
        f"""<div style="display:flex;align-items:center;gap:1rem;padding:0.9rem 0;
            border-bottom:1px solid #e5ddd0;">
            <span style="color:{p['accent']};font-weight:700;font-family:monospace;min-width:3rem;">
                {esc(s['num'])}</span>
            <span style="font-family:{f['heading']};color:{p['primary']};font-weight:500;">
                {esc(s['title'])}</span>
            <span style="flex-grow:1;border-bottom:1px dotted #c9c0b0;margin:0 0.5rem;height:0.6em;"></span>
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
    <div id="section-{esc(num)}" style="background:{p['light']};">
        <div style="background:{p['light']};padding:2.5rem 3rem;position:relative;overflow:hidden;">
            <div style="position:absolute;top:0.5rem;left:2rem;font-family:{f['heading']};font-size:7rem;
                font-weight:700;color:{p['accent']};opacity:0.12;line-height:1;">{esc(num)}</div>
            <h2 style="font-family:{f['heading']};color:{p['primary']};font-size:2.5rem;font-weight:700;
                position:relative;">{esc(title)}</h2>
            <div style="width:120px;height:5px;background:{p['accent']};margin-top:1rem;"></div>
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

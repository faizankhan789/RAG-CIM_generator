"""Chrome skeleton for the 'broker' (Business Broker, id="broker") template.

Visual language ported from a reference business-broker deal-teaser page
(forest green + gold + cream, Playfair Display / Inter pairing) — colors,
typography, and general composition (nav bar + two-column hero with a
"memorandum details" side panel). The reference is a live interactive
micro-site (JS tabs, accordions, flip-cards, canvas charts); this renderer
produces the same static, linear, print/PDF-safe document every other chrome
module does, so the JS scroll-spy/active-tab behavior doesn't carry over —
but the nav bar's tabs ARE real `<a href="#section-N">` anchors to each
actual section (and the TOC rows are too), not decorative buttons that only
open a menu.

stats_html renders as its own light band AFTER the cover+TOC, exactly like
every other chrome module (classic/editorial/startup/luxury/minimalist) —
it used to be wedged inside the dark hero panel as a bordered "Key Metrics"
box, but the component's cards are opaque light-cream cards (see
components.py's stat_strip, shared by all 6 templates), so stacking them
inside a translucent dark hero read as a solid, out-of-place block breaking
the hero's transparency instead of floating cleanly over it. Moving them
below the TOC, on their own light band, is where they were designed to sit.
The side-panel facts are the same real industry/date_str values passed to
every other template's cover — nothing here is derived, invented, or
hardcoded; see core/chrome/startup.py's docstring for the same
real-data-only rule.
"""

from __future__ import annotations

from core.templates import TEMPLATES
from core.chrome._shared import base_css, disclaimer_page, esc, section_footer, wrap_document

PALETTE = TEMPLATES["broker"]["palette"]
FONTS = TEMPLATES["broker"]["fonts"]


def _fact_row(label: str, value: str) -> str:
    return f"""
    <div style="display:flex;justify-content:space-between;gap:1rem;padding:0.55rem 0;
        border-bottom:1px solid rgba(255,255,255,0.1);">
        <span style="color:rgba(255,255,255,0.55);font-size:0.75rem;">{esc(label)}</span>
        <span style="color:#fff;font-size:0.8rem;font-weight:600;text-align:right;
            min-width:0;overflow-wrap:break-word;word-break:break-word;">{esc(value)}</span>
    </div>"""


def _topbar(business_name: str, logo_html: str) -> str:
    """Persistent nav bar — a direct child of <body> (spans the full document
    height), NOT nested inside #cover (which is only ~1 viewport tall). A
    sticky element can never stick past the bottom of its own containing
    block, so nesting it inside the 1-viewport-tall cover made it stop
    sticking the moment you scrolled past the cover — this must sit at the
    top level to stay pinned across every section. In paginated print/PDF
    output `sticky` has no effect (each page is static), which is harmless —
    the anchor itself still works there.

    No per-section tab links here — a real listing can have any number of
    sections with arbitrarily long titles, and showing them all wrapped the
    bar to 2 lines. The single "Contents" link to `#toc` is the one
    navigation affordance; the TOC page lists every real section."""
    p, f = PALETTE, FONTS
    brand = logo_html or (
        f'<span style="font-family:{f["heading"]};color:#fff;font-weight:700;font-size:1.05rem;'
        f'overflow-wrap:break-word;word-break:break-word;">{esc(business_name)}</span>'
    )
    return f"""
    <div style="display:flex;justify-content:space-between;align-items:center;gap:1.5rem;
        width:100%;padding:1.1rem 3rem;position:sticky;top:0;z-index:50;
        border-bottom:2px solid {p['accent']};background:{p['primary']};flex-wrap:wrap;">
        <div style="flex-shrink:0;">{brand}</div>
        <a href="#toc" style="text-decoration:none;background:{p['accent']};color:{p['primary']};
            font-size:0.7rem;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;
            padding:7px 18px;border-radius:20px;white-space:nowrap;flex-shrink:0;">Contents</a>
    </div>"""


def _cover(business_name: str, asking_price: str, industry: str, date_str: str) -> str:
    p, f = PALETTE, FONTS
    facts = (
        _fact_row("Industry", industry)
        + _fact_row("Prepared", date_str)
        + _fact_row("Document Type", "Confidential Memorandum")
    )
    return f"""
    <div id="cover" style="min-height:100vh;position:relative;
        background:linear-gradient(160deg,{p['primary']} 0%,#0F2D22 100%);
        display:flex;flex-direction:column;">
        <div style="flex:1;display:flex;flex-wrap:wrap;gap:2.5rem;align-items:flex-start;
            padding:3rem 3rem 2.5rem;">
            <div style="flex:2 1 480px;min-width:0;">
                <div style="display:flex;flex-wrap:wrap;gap:0.6rem;margin-bottom:1.5rem;">
                    <div style="display:inline-block;background:rgba(184,149,58,0.18);border:1px solid
                        rgba(184,149,58,0.45);border-radius:20px;padding:5px 16px;">
                        <span style="color:{p['accent']};text-transform:uppercase;letter-spacing:0.12em;
                            font-size:0.75rem;font-weight:600;">{esc(industry)}</span>
                    </div>
                    <div style="display:inline-flex;align-items:center;gap:6px;background:rgba(255,255,255,0.08);
                        border:1px solid rgba(255,255,255,0.25);border-radius:20px;padding:5px 16px;">
                        <span style="width:6px;height:6px;border-radius:50%;background:{p['accent']};
                            flex-shrink:0;"></span>
                        <span style="color:rgba(255,255,255,0.85);text-transform:uppercase;letter-spacing:0.1em;
                            font-size:0.7rem;font-weight:600;white-space:nowrap;">Confidential &mdash; NDA Required</span>
                    </div>
                </div>
                <h1 style="font-family:{f['heading']};color:#fff;font-size:3.4rem;line-height:1.15;
                    font-weight:700;max-width:800px;overflow-wrap:break-word;word-break:break-word;">
                    {esc(business_name)}</h1>
                <div style="width:70px;height:2px;background:{p['accent']};margin:1.5rem 0;"></div>
                <div style="color:rgba(255,255,255,0.55);font-size:0.8rem;letter-spacing:0.1em;
                    text-transform:uppercase;">Asking Price</div>
                <div style="font-family:{f['heading']};color:{p['accent']};font-size:2rem;font-weight:700;">
                    {esc(asking_price)}</div>
            </div>
            <div style="flex:1 1 260px;min-width:240px;max-width:320px;background:rgba(255,255,255,0.05);
                border:1px solid rgba(184,149,58,0.35);border-radius:10px;padding:1.5rem;">
                <div style="color:{p['accent']};font-size:0.7rem;letter-spacing:0.12em;
                    text-transform:uppercase;font-weight:700;margin-bottom:0.9rem;">Memorandum Details</div>
                <div style="height:1px;background:rgba(255,255,255,0.15);margin-bottom:0.4rem;"></div>
                {facts}
                <div style="margin-top:1.2rem;padding-top:1rem;border-top:1px solid rgba(255,255,255,0.15);
                    color:rgba(255,255,255,0.55);font-size:0.7rem;text-transform:uppercase;
                    letter-spacing:0.08em;">Strictly Private &amp; Confidential</div>
            </div>
        </div>
        <div style="position:relative;width:100%;padding:1rem 3rem;border-top:1px solid rgba(255,255,255,0.15);
            display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem;">
            <span style="font-size:0.75rem;color:rgba(255,255,255,0.7);font-style:italic;">
                Prepared exclusively for prospective acquirers</span>
            <span style="font-size:0.75rem;color:rgba(255,255,255,0.7);">{esc(date_str)}</span>
            <span style="font-size:0.75rem;color:rgba(255,255,255,0.7);text-transform:uppercase;
                letter-spacing:0.1em;">Strictly Private &amp; Confidential</span>
        </div>
    </div>"""


def _toc(sections: list[dict]) -> str:
    p, f = PALETTE, FONTS
    rows = "\n".join(
        f"""<a href="#section-{esc(s['num'])}" style="text-decoration:none;display:flex;
            align-items:center;gap:1rem;padding:0.9rem 0;border-bottom:1px solid #DDD6CC;">
            <span style="color:{p['accent']};font-weight:700;font-family:monospace;min-width:3rem;">
                {esc(s['num'])}</span>
            <span style="font-family:{f['heading']};color:{p['primary']};font-weight:600;">
                {esc(s['title'])}</span>
            <span style="flex-grow:1;border-bottom:1px dotted #C4BEB6;margin:0 0.5rem;height:0.6em;"></span>
            <span style="color:{p['accent']};">&#8594;</span>
        </a>"""
        for s in sections
    )
    return f"""
    <div id="toc" style="min-height:700px;background:{p['light']};padding:4rem 3rem;
        scroll-margin-top:190px;">
        <h2 style="font-family:{f['heading']};color:{p['primary']};font-size:2rem;font-weight:700;
            margin-bottom:2rem;">Table of Contents</h2>
        {rows}
    </div>"""


def render_section(num: str, title: str, body_html: str, business_name: str) -> str:
    p, f = PALETTE, FONTS
    return f"""
    <div id="section-{esc(num)}" style="background:{p['light']};scroll-margin-top:190px;">
        <div style="background:{p['light']};padding:2.5rem 3rem 1.5rem;">
            <div style="display:flex;align-items:baseline;gap:1rem;margin-bottom:0.4rem;">
                <span style="color:{p['mid']};font-family:monospace;font-size:0.9rem;">{esc(num)}</span>
                <h2 style="font-family:{f['heading']};color:{p['primary']};font-size:1.8rem;
                    font-weight:700;">{esc(title)}</h2>
            </div>
            <div style="width:100%;height:2px;background:linear-gradient(90deg,{p['accent']},
                transparent);"></div>
        </div>
        <div style="background:#fff;padding:1.5rem 3rem 3rem;">{body_html}</div>
        {section_footer(business_name, num, title, p)}
    </div>"""


def render(business_name: str, asking_price: str, industry: str, date_str: str, logo_html: str,
           stats_html: str, sections: list[dict], **_ignored) -> str:
    body = (
        _topbar(business_name, logo_html)
        + _cover(business_name, asking_price, industry, date_str)
        + _toc(sections)
        + (stats_html or "")
        + "".join(render_section(s["num"], s["title"], s["body_html"], business_name) for s in sections)
        + disclaimer_page(business_name, PALETTE)
    )
    return wrap_document(base_css(FONTS), body)

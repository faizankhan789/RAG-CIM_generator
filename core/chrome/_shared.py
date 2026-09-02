"""Template-agnostic helpers shared by every core/chrome/<id>.py module.

Only genuinely identical-across-templates pieces live here — the document-wide
CSS reset/print rules, the per-section footer bar, and the last-page
disclaimer — all taken verbatim from the base spec in core/llm.py (TECHNICAL
REQUIREMENTS, SECTION FOOTER, LAST PAGE — DISCLAIMER). Cover/TOC/section-header
band differ enough per template that each core/chrome/<id>.py implements its
own; only their shared skeleton (this module) is factored out.

Every string returned here uses INLINE styles, not class-based CSS rules —
this sidesteps the "CSS specificity trap" the LLM has to be warned about
every generation (a global `p { color }` rule silently overriding an
inherited dark-background color): an inline style always wins, so that whole
bug class can't happen in code-rendered chrome.
"""

from __future__ import annotations

import html as _html


def esc(text: str) -> str:
    """HTML-escape a piece of real listing data before interpolating it."""
    return _html.escape(text or "", quote=True)


def base_css(fonts: dict) -> str:
    """Document-wide reset/print rules every template shares (TECHNICAL REQUIREMENTS)."""
    return f"""
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        html {{ scroll-behavior: smooth; }}
        body {{ font-family: {fonts['body']}; font-variant-numeric: tabular-nums; }}
        h1, h2, h3, h4 {{ page-break-after: avoid; }}
        table, figure, ul, ol {{ page-break-inside: avoid; }}
        tr {{ page-break-inside: avoid; }}
        .cim-image {{ page-break-inside: avoid; }}
        @page {{ size: A4; margin: 10mm; }}
        @media print {{ .no-print {{ display: none; }} }}
    """


_LOCK_SVG = (
    '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="{color}" '
    'stroke-width="2" style="vertical-align:middle;margin-right:4px;flex-shrink:0;">'
    '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>'
)


def section_footer(business_name: str, num: str, title: str, palette: dict, *, lock_icon: bool = True) -> str:
    """The slim per-section footer bar — identical structure on every template
    (SECTION FOOTER spec); only palette colors differ."""
    lock = _LOCK_SVG.format(color=palette["mid"]) if lock_icon else ""
    return f"""
    <div style="display:flex;justify-content:space-between;align-items:center;
        padding:0.75rem 3rem;border-top:1px solid {palette['accent']};background:{palette['light']};
        flex-wrap:wrap;gap:0.5rem;">
        <span style="font-size:0.75rem;color:{palette['mid']};">{esc(business_name)}</span>
        <span style="font-size:0.75rem;color:{palette['mid']};text-transform:uppercase;
            letter-spacing:0.08em;display:flex;align-items:center;">
            {lock}STRICTLY PRIVATE &amp; CONFIDENTIAL
        </span>
        <span style="font-size:0.75rem;color:{palette['mid']};">{esc(num)}. {esc(title)}</span>
    </div>"""


def disclaimer_page(business_name: str, palette: dict) -> str:
    """LAST PAGE — DISCLAIMER, identical skeleton on every template (dark
    primary-bg page); text color is set inline, so it can never be silently
    overridden by an unrelated global `p{color}` rule the way freeform
    LLM-authored HTML has to guard against."""
    return f"""
    <div id="disclaimer" style="background:{palette['primary']};padding:4rem 3rem;text-align:center;">
        <h2 style="color:{palette['accent']};font-size:1.5rem;letter-spacing:0.08em;
            text-transform:uppercase;margin-bottom:2rem;">Important Notice &amp; Disclaimer</h2>
        <div style="width:80px;height:2px;background:{palette['accent']};margin:0 auto 2rem;"></div>
        <div style="max-width:700px;margin:0 auto;">
            <p style="color:rgba(255,255,255,0.85);line-height:1.8;font-size:0.9rem;">
                This Confidential Information Memorandum (the &quot;CIM&quot;) has been prepared
                exclusively for prospective acquirers of {esc(business_name)}. The contents of this
                CIM are strictly confidential and proprietary. Distribution, reproduction, or
                disclosure of any portion of this document to unauthorized third parties is strictly
                prohibited without the express written consent of the seller and the listing broker.
            </p>
        </div>
    </div>"""


def wrap_document(style_block: str, body_html: str) -> str:
    """Final <!DOCTYPE html> envelope — identical shape for every template."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>Confidential Information Memorandum</title>
<style>
{style_block}
</style>
</head>
<body>
{body_html}
</body>
</html>"""

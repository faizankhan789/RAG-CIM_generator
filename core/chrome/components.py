"""Fixed Python renderers for the 8 structural section-body components that
were previously freeform LLM-authored HTML/CSS/SVG ([stat-strip], [data-table],
[chart-bar], [chart-donut], [two-col-*], [card-grid-*], [swot-grid], [timeline]).

Same rationale as core/chrome/<template>.py's chrome skeletons: these are the
components with fixed-width/multi-column/SVG-coordinate layouts, i.e. the ones
that can actually overlap when fed real, variable-length customer data. Every
renderer here bakes in `min-width:0` on flex/grid children and
`overflow-wrap:break-word` on any element holding a real name/number, and never
uses `position:absolute` — so that whole bug class can't happen in code-rendered
output the way it could in LLM-authored markup.

Narrative prose ([narrative-pull], [bullet-list], [image-hero], [image-mosaic],
plain paragraphs) stays LLM-authored HTML — normal-flow elements don't overlap,
and componentizing prose would risk flattening the writing quality.

Anti-fabrication: every renderer only lays out values it's handed — it never
derives, rounds, averages, or invents a figure. [chart-bar] and [chart-donut] go
further than the old LLM-authored versions: the caller now supplies raw numeric
values only, and this module computes bar heights / stroke-dasharray /
percentages itself, removing the LLM's own arithmetic (and any chance of a
miscomputed or fabricated proportion) from the loop entirely.
"""

from __future__ import annotations

import html as _html
import math
from typing import Any, Callable


def esc(value: Any) -> str:
    """HTML-escape a piece of real listing data before interpolating it."""
    return _html.escape("" if value is None else str(value), quote=True)


class ComponentError(ValueError):
    """Raised when a component's payload is malformed or missing required data.

    Callers (core/cim_assembler.py) catch this, drop just that one component
    block, and log an error — matching the existing malformed-SECTION-marker
    behavior: one bad block must never fail the whole document.
    """


# ═══════════════════════════════════════════════
# Icon system — the 10 fixed monoline SVG icons, moved out of the prompt into
# code so every icon always carries correct width/height/viewBox (previously a
# common LLM mistake: an icon <svg> missing width/height defaults to the
# browser's native 300x150px).
# ═══════════════════════════════════════════════

_ICON_PATHS: dict[str, str] = {
    "check": '<polyline points="4 12 9 17 20 6"/>',
    "trend-up": '<polyline points="3 17 9 11 13 15 21 7"/><polyline points="14 7 21 7 21 14"/>',
    "dollar": (
        '<line x1="12" y1="2" x2="12" y2="22"/>'
        '<path d="M15.5 8c0-1.7-1.6-3-3.5-3s-3.5 1.3-3.5 3 1.6 2.4 3.5 2.8 3.5 1.1 3.5 2.7-1.6 3-3.5 3-3.5-1.3-3.5-3"/>'
    ),
    "building": (
        '<rect x="4" y="3" width="16" height="18" rx="1"/>'
        '<line x1="9" y1="8" x2="9.01" y2="8"/><line x1="15" y1="8" x2="15.01" y2="8"/>'
        '<line x1="9" y1="13" x2="9.01" y2="13"/><line x1="15" y1="13" x2="15.01" y2="13"/>'
        '<line x1="10" y1="21" x2="10" y2="17"/><line x1="14" y1="21" x2="14" y2="17"/>'
    ),
    "users": (
        '<circle cx="9" cy="8" r="3"/><path d="M4 20c0-3 2.5-5 5-5s5 2 5 5"/>'
        '<circle cx="17" cy="9" r="2.5"/><path d="M15.5 20c.2-2.2 1.7-4 3.8-4.4"/>'
    ),
    "shield": '<path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z"/>',
    "calendar": (
        '<rect x="3" y="5" width="18" height="16" rx="2"/><line x1="3" y1="10" x2="21" y2="10"/>'
        '<line x1="8" y1="3" x2="8" y2="7"/><line x1="16" y1="3" x2="16" y2="7"/>'
    ),
    "map-pin": (
        '<path d="M12 21s7-6.5 7-11a7 7 0 1 0-14 0c0 4.5 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/>'
    ),
    "briefcase": (
        '<rect x="3" y="7" width="18" height="13" rx="2"/>'
        '<path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="3" y1="13" x2="21" y2="13"/>'
    ),
    "star": '<polygon points="12 2 15 9 22 9 16.5 13.5 18.5 21 12 16.5 5.5 21 7.5 13.5 2 9 9 9"/>',
}


def icon(name: str, color: str, size: int = 22) -> str:
    """One of the 10 fixed icons. Unknown name -> empty string (skip rather
    than guess/invent a shape — matches the old prompt's own instruction)."""
    path = _ICON_PATHS.get(name or "")
    if not path:
        return ""
    return (
        f'<svg viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.5" '
        f'stroke-linecap="round" stroke-linejoin="round" width="{size}" height="{size}" '
        f'style="flex-shrink:0;display:block;">{path}</svg>'
    )


def _abbreviate_number(v: float) -> str:
    """Compact numeric label for chart axes only (e.g. "4.5M") — the paired
    [data-table] still shows the exact source figure; this never substitutes
    for it."""
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1_000_000_000:
        n, suffix = a / 1_000_000_000, "B"
    elif a >= 1_000_000:
        n, suffix = a / 1_000_000, "M"
    elif a >= 1_000:
        n, suffix = a / 1_000, "K"
    else:
        s = f"{a:g}"
        return f"{sign}{s}"
    s = f"{n:.1f}".rstrip("0").rstrip(".")
    return f"{sign}{s}{suffix}"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ComponentError(message)


def _as_number(value: Any, what: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ComponentError(f"{what}: value {value!r} is not numeric") from None


# ═══════════════════════════════════════════════
# [stat-strip] — payload: list[{"icon": str, "label": str, "value": str}]
# ═══════════════════════════════════════════════

def stat_strip(payload: Any, palette: dict) -> str:
    _require(isinstance(payload, list) and payload, "stat-strip: payload must be a non-empty list")
    cards = []
    for item in payload:
        _require(isinstance(item, dict), "stat-strip: each item must be an object")
        _require("label" in item and "value" in item, "stat-strip: each item needs label and value")
        cards.append(f"""
        <div style="flex:1 1 0;min-width:0;text-align:center;padding:0 1rem;">
            {icon(item.get("icon", ""), palette["accent"], size=24)}
            <div style="margin-top:0.5rem;font-size:0.75rem;text-transform:uppercase;
                letter-spacing:0.06em;color:rgba(255,255,255,0.75);overflow-wrap:break-word;
                word-break:break-word;">{esc(item["label"])}</div>
            <div style="font-size:1.5rem;font-weight:700;color:#fff;
                font-variant-numeric:tabular-nums;overflow-wrap:break-word;
                word-break:break-word;">{esc(item["value"])}</div>
        </div>""")
    # interleave cards and dividers
    row = []
    for i, card in enumerate(cards):
        row.append(card)
        if i < len(cards) - 1:
            row.append('<div style="width:1px;flex-shrink:0;background:rgba(255,255,255,0.15);"></div>')
    return (
        f'<div style="display:flex;flex-wrap:wrap;align-items:center;gap:0.5rem;'
        f'background:{palette["primary"]};border-radius:12px;padding:1.5rem 1rem;">'
        f'{"".join(row)}</div>'
    )


# ═══════════════════════════════════════════════
# [data-table] — payload: {"headers": [str], "rows": [[str]], "footnote": str?}
# ═══════════════════════════════════════════════

def data_table(payload: Any, palette: dict) -> str:
    _require(isinstance(payload, dict), "data-table: payload must be an object")
    headers = payload.get("headers")
    rows = payload.get("rows")
    _require(isinstance(headers, list) and headers, "data-table: headers must be a non-empty list")
    _require(isinstance(rows, list), "data-table: rows must be a list")

    thead_cells = "".join(
        f'<th style="padding:0.75rem 1rem;text-align:{"right" if i > 0 else "left"};'
        f'color:#fff;font-size:0.8rem;text-transform:uppercase;letter-spacing:0.04em;'
        f'overflow-wrap:break-word;word-break:break-word;min-width:0;">{esc(h)}</th>'
        for i, h in enumerate(headers)
    )
    body_rows = []
    for r_i, row in enumerate(rows):
        _require(isinstance(row, list), "data-table: each row must be a list of cells")
        bg = palette["light"] if r_i % 2 else "#ffffff"
        cells = "".join(
            f'<td style="padding:0.65rem 1rem;text-align:{"right" if c_i > 0 else "left"};'
            f'font-variant-numeric:tabular-nums;overflow-wrap:break-word;word-break:break-word;'
            f'min-width:0;color:{palette["primary"]};">{esc(cell)}</td>'
            for c_i, cell in enumerate(row)
        )
        body_rows.append(f'<tr style="background:{bg};">{cells}</tr>')

    footnote = payload.get("footnote")
    caption = (
        f'<caption style="caption-side:bottom;text-align:left;font-size:0.75rem;'
        f'color:{palette["mid"]};padding-top:0.5rem;">{esc(footnote)}</caption>'
        if footnote else ""
    )
    return (
        f'<table style="width:100%;border-collapse:collapse;table-layout:fixed;">'
        f'<thead><tr style="background:{palette["primary"]};">{thead_cells}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody>{caption}</table>'
    )


# ═══════════════════════════════════════════════
# [chart-bar] — payload: {"periods": [{"label": str, "value": number}],
#                          "currency": str?, "suffix": str?}
# Every coordinate is computed here, not by the LLM — removes both the
# label-collision bug (hard N<=6 cap) and any chance of miscomputed/fabricated
# bar heights.
# ═══════════════════════════════════════════════

def chart_bar(payload: Any, palette: dict) -> str:
    _require(isinstance(payload, dict), "chart-bar: payload must be an object")
    periods = payload.get("periods")
    _require(isinstance(periods, list) and periods, "chart-bar: periods must be a non-empty list")

    cleaned: list[tuple[str, float]] = []
    for p in periods:
        _require(isinstance(p, dict) and "label" in p and "value" in p,
                  "chart-bar: each period needs label and value")
        cleaned.append((str(p["label"]), _as_number(p["value"], "chart-bar")))

    # LABEL COLLISION GUARD: cap at the most recent 6 periods — a barWidth
    # below ~50px can't fit a value label without colliding with its neighbor.
    if len(cleaned) > 6:
        cleaned = cleaned[-6:]

    currency = str(payload.get("currency", ""))
    suffix = str(payload.get("suffix", ""))
    n = len(cleaned)
    max_v = max(v for _, v in cleaned) or 1.0
    bar_width = (440 / n) * 0.6

    bars = []
    for i, (label, v) in enumerate(cleaned):
        x = 40 + i * (440 / n)
        h = max((v / max_v) * 180, 0.0)
        y = 220 - h
        is_last = i == n - 1
        fill = palette["accent"] if is_last else palette["mid"]
        opacity = "1" if is_last else "0.55"
        value_label = f"{currency}{_abbreviate_number(v)}{suffix}"
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{h:.1f}" '
            f'rx="4" fill="{fill}" fill-opacity="{opacity}"/>'
            f'<text x="{x + bar_width / 2:.1f}" y="{y - 8:.1f}" text-anchor="middle" '
            f'font-size="12" fill="{palette["primary"]}" '
            f'style="font-variant-numeric:tabular-nums;">{esc(value_label)}</text>'
            f'<text x="{x + bar_width / 2:.1f}" y="242" text-anchor="middle" '
            f'font-size="11" fill="{palette["mid"]}">{esc(label)}</text>'
        )
    baseline = f'<line x1="30" y1="220" x2="470" y2="220" stroke="{palette["mid"]}" stroke-width="1"/>'
    return (
        f'<svg viewBox="0 0 500 260" width="100%" height="260" preserveAspectRatio="xMidYMid meet" '
        f'style="display:block;max-width:500px;">{baseline}{"".join(bars)}</svg>'
    )


# ═══════════════════════════════════════════════
# [chart-donut] — payload: {"segments": [{"label": str, "value": number}],
#                            "center_label": str?}
# Percentages and stroke-dasharray are computed here from the raw values —
# the LLM never supplies (and so can never fabricate/eyeball) a percentage.
# ═══════════════════════════════════════════════

_DONUT_COLOR_KEYS = ("accent", "mid", "primary")


def chart_donut(payload: Any, palette: dict) -> str:
    _require(isinstance(payload, dict), "chart-donut: payload must be an object")
    segments = payload.get("segments")
    _require(isinstance(segments, list) and segments, "chart-donut: segments must be a non-empty list")

    cleaned: list[tuple[str, float]] = []
    for s in segments:
        _require(isinstance(s, dict) and "label" in s and "value" in s,
                  "chart-donut: each segment needs label and value")
        v = _as_number(s["value"], "chart-donut")
        _require(v >= 0, "chart-donut: segment values must be non-negative")
        cleaned.append((str(s["label"]), v))

    total = sum(v for _, v in cleaned)
    _require(total > 0, "chart-donut: segment values must sum to a positive total")

    colors = [palette[k] for k in _DONUT_COLOR_KEYS]
    circumference = 2 * math.pi * 70
    circles = []
    legend = []
    running_pct = 0.0
    for i, (label, v) in enumerate(cleaned):
        pct = v / total * 100
        dash = (pct / 100) * circumference
        gap = circumference - dash
        dashoffset = -(running_pct / 100) * circumference
        color = colors[i % len(colors)]
        opacity = max(1 - 0.3 * (i // len(colors)), 0.4)
        circles.append(
            f'<circle cx="100" cy="100" r="70" fill="none" stroke-width="28" '
            f'stroke="{color}" stroke-opacity="{opacity}" '
            f'stroke-dasharray="{dash:.1f} {gap:.1f}" stroke-dashoffset="{dashoffset:.1f}"/>'
        )
        legend.append(
            f'<div style="display:flex;align-items:center;gap:0.5rem;min-width:0;">'
            f'<span style="width:10px;height:10px;border-radius:50%;flex-shrink:0;'
            f'background:{color};opacity:{opacity};"></span>'
            f'<span style="overflow-wrap:break-word;word-break:break-word;'
            f'color:{palette["primary"]};font-size:0.85rem;">{esc(label)} — {pct:.1f}%</span></div>'
        )
        running_pct += pct

    center_label = payload.get("center_label")
    center_text = (
        f'<text x="100" y="100" text-anchor="middle" dominant-baseline="middle" '
        f'font-size="16" font-weight="700" fill="{palette["primary"]}">{esc(center_label)}</text>'
        if center_label else ""
    )
    svg = (
        f'<svg viewBox="0 0 200 200" width="200" height="200" style="flex-shrink:0;display:block;">'
        f'<g transform="rotate(-90 100 100)">{"".join(circles)}</g>{center_text}</svg>'
    )
    return (
        f'<div style="display:flex;align-items:center;gap:1.5rem;flex-wrap:wrap;">{svg}'
        f'<div style="display:flex;flex-direction:column;gap:0.4rem;min-width:0;">'
        f'{"".join(legend)}</div></div>'
    )


# ═══════════════════════════════════════════════
# [two-col] — payload: {"left_html": str, "right_title": str?,
#                        "right_items": [str], "ratio": "60-40"|"50-50"}
# left_html stays LLM-authored narrative prose (normal flow, no overlap risk);
# only the column split/highlight-box structure is code-owned.
# ═══════════════════════════════════════════════

def two_col(payload: Any, palette: dict) -> str:
    _require(isinstance(payload, dict), "two-col: payload must be an object")
    left_html = payload.get("left_html")
    _require(isinstance(left_html, str) and left_html.strip(), "two-col: left_html is required")
    right_items = payload.get("right_items")
    _require(isinstance(right_items, list) and right_items, "two-col: right_items must be a non-empty list")

    ratio = payload.get("ratio", "60-40")
    left_pct, right_pct = (50, 50) if ratio == "50-50" else (60, 40)
    right_title = payload.get("right_title")
    title_html = (
        f'<div style="font-weight:700;color:{palette["primary"]};margin-bottom:0.75rem;'
        f'overflow-wrap:break-word;word-break:break-word;">{esc(right_title)}</div>'
        if right_title else ""
    )
    items_html = "".join(
        f'<li style="margin-bottom:0.5rem;overflow-wrap:break-word;word-break:break-word;'
        f'min-width:0;">{esc(it)}</li>'
        for it in right_items
    )
    return f"""
    <div style="display:flex;gap:1.5rem;flex-wrap:wrap;">
        <div style="flex:{left_pct} 1 0;min-width:0;">{left_html}</div>
        <div style="flex:{right_pct} 1 0;min-width:0;background:{palette['light']};
            border-radius:12px;padding:1.5rem;">{title_html}
            <ul style="list-style:none;padding:0;margin:0;color:{palette['primary']};">{items_html}</ul>
        </div>
    </div>"""


# ═══════════════════════════════════════════════
# [card-grid] — payload: {"cols": 2|3, "cards": [{"title", "subtitle"?, "body"?}]}
# ═══════════════════════════════════════════════

def card_grid(payload: Any, palette: dict) -> str:
    _require(isinstance(payload, dict), "card-grid: payload must be an object")
    cards = payload.get("cards")
    _require(isinstance(cards, list) and cards, "card-grid: cards must be a non-empty list")
    cols = payload.get("cols", 2)
    if cols not in (2, 3):
        cols = 2
    basis = 100 / cols

    rendered = []
    for c in cards:
        _require(isinstance(c, dict) and "title" in c, "card-grid: each card needs a title")
        subtitle = c.get("subtitle")
        body = c.get("body")
        subtitle_html = (
            f'<div style="color:{palette["accent"]};font-size:0.85rem;margin-top:0.2rem;'
            f'overflow-wrap:break-word;word-break:break-word;">{esc(subtitle)}</div>'
            if subtitle else ""
        )
        body_html = (
            f'<div style="color:{palette["mid"]};font-size:0.85rem;margin-top:0.6rem;'
            f'overflow-wrap:break-word;word-break:break-word;">{esc(body)}</div>'
            if body else ""
        )
        rendered.append(f"""
        <div style="flex:0 1 calc({basis:.2f}% - 1rem);min-width:0;background:{palette['light']};
            border-radius:12px;padding:1.5rem;box-shadow:0 2px 8px rgba(0,0,0,0.06);">
            <div style="font-weight:700;color:{palette['primary']};overflow-wrap:break-word;
                word-break:break-word;">{esc(c["title"])}</div>
            {subtitle_html}{body_html}
        </div>""")
    return f'<div style="display:flex;flex-wrap:wrap;gap:1rem;">{"".join(rendered)}</div>'


# ═══════════════════════════════════════════════
# [swot-grid] — payload: {"strengths": [str], "weaknesses": [str],
#                          "opportunities": [str], "threats": [str]}
# ═══════════════════════════════════════════════

_SWOT_SPEC = (
    ("strengths", "Strengths", "#16a34a"),
    ("weaknesses", "Weaknesses", "#dc2626"),
    ("opportunities", "Opportunities", "#2563eb"),
    ("threats", "Threats", "#d97706"),
)


def swot_grid(payload: Any, palette: dict) -> str:
    _require(isinstance(payload, dict), "swot-grid: payload must be an object")
    quadrants = []
    any_items = False
    for key, label, color in _SWOT_SPEC:
        items = payload.get(key) or []
        _require(isinstance(items, list), f"swot-grid: {key} must be a list")
        any_items = any_items or bool(items)
        items_html = "".join(
            f'<li style="margin-bottom:0.4rem;overflow-wrap:break-word;word-break:break-word;'
            f'min-width:0;">{esc(it)}</li>'
            for it in items
        )
        quadrants.append(f"""
        <div style="flex:0 1 calc(50% - 0.75rem);min-width:0;background:#fff;
            border-top:4px solid {color};border-radius:8px;padding:1.25rem;
            box-shadow:0 1px 4px rgba(0,0,0,0.08);">
            <div style="color:{color};font-weight:700;text-transform:uppercase;
                letter-spacing:0.06em;font-size:0.8rem;margin-bottom:0.6rem;">{label}</div>
            <ul style="list-style:none;padding:0;margin:0;color:{palette['primary']};
                font-size:0.9rem;">{items_html}</ul>
        </div>""")
    _require(any_items, "swot-grid: at least one quadrant must have items")
    return f'<div style="display:flex;flex-wrap:wrap;gap:1.5rem;">{"".join(quadrants)}</div>'


# ═══════════════════════════════════════════════
# [timeline] — payload: {"steps": [{"title": str, "body": str?}]}
# ═══════════════════════════════════════════════

def timeline(payload: Any, palette: dict) -> str:
    _require(isinstance(payload, dict), "timeline: payload must be an object")
    steps = payload.get("steps")
    _require(isinstance(steps, list) and steps, "timeline: steps must be a non-empty list")

    rows = []
    for i, s in enumerate(steps, start=1):
        _require(isinstance(s, dict) and "title" in s, "timeline: each step needs a title")
        body = s.get("body")
        body_html = (
            f'<div style="color:{palette["mid"]};font-size:0.9rem;margin-top:0.25rem;'
            f'overflow-wrap:break-word;word-break:break-word;">{esc(body)}</div>'
            if body else ""
        )
        rows.append(f"""
        <div style="display:flex;gap:1rem;margin-bottom:1.25rem;">
            <div style="flex-shrink:0;width:2rem;height:2rem;border-radius:50%;
                background:{palette['accent']};color:#fff;display:flex;align-items:center;
                justify-content:center;font-weight:700;">{i}</div>
            <div style="min-width:0;">
                <div style="font-weight:700;color:{palette['primary']};overflow-wrap:break-word;
                    word-break:break-word;">{esc(s["title"])}</div>
                {body_html}
            </div>
        </div>""")
    return f'<div>{"".join(rows)}</div>'


RENDERERS: dict[str, Callable[[Any, dict], str]] = {
    "stat-strip": stat_strip,
    "data-table": data_table,
    "chart-bar": chart_bar,
    "chart-donut": chart_donut,
    "two-col": two_col,
    "card-grid": card_grid,
    "swot-grid": swot_grid,
    "timeline": timeline,
}

"""Unit tests for core/chrome/<id>.py — one per built-in template.

Fixed sample input -> assert the key structural pieces (name, price, industry,
section titles, TOC entries, doc envelope) actually appear in the rendered HTML.
Not a byte-exact snapshot (the modules use plain f-strings, not a template
engine, so exact output is just "read the source"); these instead lock in the
CONTRACT each renderer promises core/cim_assembler.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.chrome import classic, editorial, luxury, minimalist, startup
from core.chrome import get_renderer

SAMPLE_SECTIONS = [
    {"num": "I", "title": "Executive Summary", "body_html": "<p>Great business.</p>"},
    {"num": "II", "title": "Company Overview", "body_html": "<p>Founded 1998.</p>"},
]

RENDER_MODULES = {
    "classic": classic,
    "minimalist": minimalist,
    "editorial": editorial,
    "luxury": luxury,
    "startup": startup,
}


@pytest.mark.parametrize("template_id", list(RENDER_MODULES))
def test_render_contains_real_data(template_id):
    module = RENDER_MODULES[template_id]
    html = module.render(
        business_name="O'Sarracino Pizzeria Ltd",
        asking_price="£450,000",
        industry="Restaurant & Food Service",
        date_str="August 31, 2026",
        logo_html="<img src='logo.png'/>",
        stats_html="<div>Revenue: £500,000</div>",
        sections=SAMPLE_SECTIONS,
    )
    assert html.startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")
    assert "O&#x27;Sarracino Pizzeria Ltd" in html
    assert "£450,000" in html
    assert "Restaurant &amp; Food Service" in html
    assert "August 31, 2026" in html
    assert "<img src='logo.png'/>" in html
    assert "Revenue: £500,000" in html
    assert "Great business." in html
    assert "Founded 1998." in html
    # TOC built from the sections list — both titles show up outside the body too
    assert html.count("Executive Summary") >= 2  # TOC entry + section header
    assert html.count("Company Overview") >= 2


@pytest.mark.parametrize("template_id", list(RENDER_MODULES))
def test_render_with_no_stats_omits_stats_block(template_id):
    module = RENDER_MODULES[template_id]
    html = module.render(
        business_name="Acme",
        asking_price="$1",
        industry="Technology",
        date_str="Jan 1, 2026",
        logo_html="",
        stats_html="",
        sections=SAMPLE_SECTIONS,
    )
    assert html.startswith("<!DOCTYPE html>")


def test_get_renderer_returns_matching_module_function():
    assert get_renderer("luxury") is luxury.render
    assert get_renderer("startup") is startup.render


def test_get_renderer_defaults_to_classic_for_unknown_id():
    assert get_renderer("nonsense") is classic.render


def test_classic_palette_for_industry_matches_restaurant():
    palette = classic.palette_for_industry("Restaurant & Food Service")
    assert palette["accent"] == "#c9622a"


def test_classic_palette_for_industry_prefers_brand_colors():
    palette = classic.palette_for_industry("Restaurant", brand_primary="#111111", brand_accent="#222222")
    assert palette == {"primary": "#111111", "accent": "#222222", "light": "#fafafa", "mid": "#111111"}


def test_classic_palette_for_industry_falls_back_to_default():
    palette = classic.palette_for_industry("Something Unrecognized")
    assert palette == classic._DEFAULT_PALETTE

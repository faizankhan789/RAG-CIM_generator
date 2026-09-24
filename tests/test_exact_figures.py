"""core.llm._restore_exact_figures — the model abbreviates/rounds money figures
($14,200,000 -> $14.2M) despite the FINANCIAL NUMBER RULES (seen in every real
run). The guard swaps an abbreviation back to the exact source figure it rounds."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from core.llm import _restore_exact_figures

SOURCE = """FY2023 revenue: $11,800,000. FY2025 revenue: $14,200,000.
Adjusted EBITDA $3,950,000. Insurance: $15,000,000 combined. Debt $2,100,000."""


def test_abbreviated_figures_are_restored_to_exact_source_values():
    html = "<p>Revenue grew to $14.2M from $11.8M; EBITDA $3.95M.</p>"
    out = _restore_exact_figures(html, SOURCE)
    assert out == "<p>Revenue grew to $14,200,000 from $11,800,000; EBITDA $3,950,000.</p>"


def test_whole_number_and_word_forms():
    html = "<p>Coverage of $15M and debt of $2.1 million.</p>"
    out = _restore_exact_figures(html, SOURCE)
    assert out == "<p>Coverage of $15,000,000 and debt of $2,100,000.</p>"


def test_svg_chart_labels_are_restored_too():
    html = '<svg width="500"><text x="10" y="20">$14.2M</text></svg>'
    assert "$14,200,000" in _restore_exact_figures(html, SOURCE)


def test_figure_with_no_source_match_is_left_alone():
    # A computed figure ($170k) has no exact source value to restore it to.
    html = "<p>a 5% cost rise would cut EBITDA by ~$170k</p>"
    assert _restore_exact_figures(html, SOURCE) == html


def test_ambiguous_abbreviation_is_left_alone():
    source = "Property A sold for $2,140,000. Property B sold for $2,050,000."
    html = "<p>about $2.1M each</p>"
    assert _restore_exact_figures(html, source) == html


def test_abbreviation_written_that_way_in_the_source_is_kept():
    source = "Revenue of $14.2M in FY2025 (per broker notes). Also $14,200,000 in the P&L."
    html = "<p>Revenue of $14.2M</p>"
    assert _restore_exact_figures(html, source) == html


def test_css_and_attributes_are_untouched():
    html = ('<style>.w{width:$14.2M}</style>'
            '<div title="$14.2M"><p>$14.2M</p></div>')
    out = _restore_exact_figures(html, SOURCE)
    assert '<style>.w{width:$14.2M}</style>' in out
    assert 'title="$14.2M"' in out
    assert "<p>$14,200,000</p>" in out


def test_exact_figures_and_percentages_are_untouched():
    html = "<p>$14,200,000 revenue, 27.8% margin, 212 keys</p>"
    assert _restore_exact_figures(html, SOURCE) == html


def test_pound_and_euro_figures():
    source = "Turnover £123,450,000 and €4,500,000 capex."
    html = "<p>£123.45M turnover, €4.5M capex</p>"
    assert _restore_exact_figures(html, source) == "<p>£123,450,000 turnover, €4,500,000 capex</p>"


def test_crm_values_without_currency_symbol_are_used_as_fallback():
    # listing_xml carries raw CRM values, e.g. <AskingPrice>8500000.00</AskingPrice>
    source = "<ListingContext><AskingPrice>8500000.00</AskingPrice><Zip>17045</Zip></ListingContext>"
    out = _restore_exact_figures("<p>Asking $8.5M; region $17k median</p>", source)
    assert "$8,500,000" in out
    assert "$17k" in out   # a 5-digit ZIP code is never treated as a money figure


import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _client_replying(text: str):
    client = MagicMock()
    final_msg = MagicMock(content=[MagicMock(text=text)],
                          usage=MagicMock(input_tokens=1, output_tokens=1), stop_reason="end_turn")
    stream_cm = MagicMock()
    stream_cm.__aenter__ = AsyncMock(return_value=MagicMock(get_final_message=AsyncMock(return_value=final_msg)))
    stream_cm.__aexit__ = AsyncMock(return_value=False)
    client.messages.stream = lambda **kw: stream_cm
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("custom", [True, False])
async def test_generate_cim_html_restores_figures_on_both_paths(custom):
    from core.llm import generate_cim_html
    reply = ("<!DOCTYPE html><html><body><p>Revenue $14.2M</p></body></html>" if custom
             else '<!-- SECTION title="I. Executive Summary" --><p>Revenue $14.2M</p><!-- /SECTION -->')
    custom_template = {"name": "T", "allow_brand_override": False, "palette": None} if custom else None
    with patch("core.llm.get_client", return_value=_client_replying(reply)):
        out = await generate_cim_html(
            all_findings=["FY2025 revenue: $14,200,000."], listing_xml="", listing_name="Acme",
            asking_price="", template_id="minimalist", custom_template=custom_template,
        )
    assert "$14,200,000" in out
    assert "$14.2M" not in out


def test_capitalised_word_units_are_restored():
    out = _restore_exact_figures("<p>$14.2 Million revenue, $2.1 MILLION debt</p>", SOURCE)
    assert out == "<p>$14,200,000 revenue, $2,100,000 debt</p>"

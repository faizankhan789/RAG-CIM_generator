"""Tests for core/cim_assembler.py — the marker parser and its error handling.

See docs/superpowers/specs/2026-08-31-cim-chrome-templates-design.md, "Error
Handling" and "Testing" sections, for the behaviors locked in here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.cim_assembler import _parse_industry, _parse_sections, _parse_stats, assemble

WELL_FORMED = """\
<!-- INDUSTRY: Restaurant & Food Service -->
<!-- STATS -->
<div>Revenue: £500,000</div>
<!-- /STATS -->
<!-- SECTION num="I" title="Executive Summary" -->
<p>O'Sarracino Pizzeria is a beloved neighborhood institution.</p>
<!-- /SECTION -->
<!-- SECTION num="II" title="Company Overview" -->
<p>Founded in 1998.</p>
<!-- /SECTION -->
"""


class TestParseIndustry:
    def test_well_formed(self):
        assert _parse_industry(WELL_FORMED) == "Restaurant & Food Service"

    def test_missing_falls_back(self):
        assert _parse_industry("<!-- SECTION num=\"I\" title=\"X\" -->b<!-- /SECTION -->") == "PRIVATE BUSINESS SALE"

    def test_empty_value_falls_back(self):
        assert _parse_industry("<!-- INDUSTRY:   -->") == "PRIVATE BUSINESS SALE"


class TestParseStats:
    def test_present(self):
        assert "£500,000" in _parse_stats(WELL_FORMED)

    def test_absent_is_empty_string(self):
        assert _parse_stats("<!-- INDUSTRY: Tech -->") == ""


class TestParseSections:
    def test_well_formed_returns_all_sections_in_order(self):
        sections = _parse_sections(WELL_FORMED)
        assert [s["num"] for s in sections] == ["I", "II"]
        assert sections[0]["title"] == "Executive Summary"
        assert "beloved neighborhood institution" in sections[0]["body_html"]

    def test_no_markers_at_all_returns_empty_list(self):
        assert _parse_sections("<!-- INDUSTRY: Tech --><p>no sections here</p>") == []

    def test_malformed_unclosed_section_is_skipped_not_fatal(self, caplog):
        mixed = (
            '<!-- INDUSTRY: Tech -->\n'
            '<!-- SECTION num="I" title="Good" -->fine<!-- /SECTION -->\n'
            '<!-- SECTION num="II" title="Broken" -->never closed'
        )
        sections = _parse_sections(mixed)
        assert len(sections) == 1
        assert sections[0]["title"] == "Good"

    def test_duplicate_section_numbers_both_parsed(self):
        dup = (
            '<!-- SECTION num="I" title="A" -->a<!-- /SECTION -->\n'
            '<!-- SECTION num="I" title="B" -->b<!-- /SECTION -->'
        )
        sections = _parse_sections(dup)
        assert len(sections) == 2
        assert [s["title"] for s in sections] == ["A", "B"]

    def test_apostrophe_in_body_survives_parsing(self):
        sections = _parse_sections(WELL_FORMED)
        assert "O'Sarracino" in sections[0]["body_html"]


class TestAssemble:
    def test_well_formed_renders_full_document(self):
        html = assemble(WELL_FORMED, "classic", "O'Sarracino Pizzeria Ltd", "£450,000", "August 31, 2026")
        assert html.startswith("<!DOCTYPE html>")
        assert "Restaurant &amp; Food Service" in html
        assert "£450,000" in html
        assert "O&#x27;Sarracino Pizzeria Ltd" in html

    def test_no_sections_falls_back_to_raw_text(self):
        raw = "<!-- INDUSTRY: Tech --><p>old-style full HTML leaked through</p>"
        assert assemble(raw, "classic", "Biz", "$1", "d") == raw

    def test_unknown_template_id_defaults_to_classic_renderer(self):
        html = assemble(WELL_FORMED, "nonexistent-id", "Biz", "$1", "d")
        assert html.startswith("<!DOCTYPE html>")

    def test_missing_industry_uses_fallback_label(self):
        no_industry = '<!-- SECTION num="I" title="X" -->body<!-- /SECTION -->'
        html = assemble(no_industry, "classic", "Biz", "$1", "d")
        assert "PRIVATE BUSINESS SALE" in html

    def test_forwards_brand_colors_to_classic_renderer(self):
        html = assemble(
            WELL_FORMED, "classic", "Biz", "$1", "d",
            brand_primary="#111111", brand_accent="#222222",
        )
        assert "#111111" in html
        assert "#222222" in html

    def test_builds_logo_img_tag_from_real_bytes(self):
        html = assemble(WELL_FORMED, "classic", "Biz", "$1", "d", logo_b64="Zm9v", logo_mime="image/png")
        assert 'src="data:image/png;base64,Zm9v"' in html

    def test_no_logo_bytes_means_no_img_tag(self):
        html = assemble(WELL_FORMED, "classic", "Biz", "$1", "d")
        assert "<img" not in html


class TestComponentBlocks:
    def test_c_block_in_stats_renders_component(self):
        marker_text = (
            '<!-- INDUSTRY: Tech -->\n'
            '<!-- STATS -->\n'
            '<!-- C:stat-strip -->[{"label":"Annual Revenue","value":"$2,400,000"}]<!-- /C -->\n'
            '<!-- /STATS -->\n'
            '<!-- SECTION num="I" title="X" -->body<!-- /SECTION -->'
        )
        html = assemble(marker_text, "classic", "Biz", "$1", "d")
        assert "$2,400,000" in html

    def test_c_block_in_section_body_renders_component(self):
        marker_text = (
            '<!-- SECTION num="I" title="Financials" -->\n'
            '<!-- C:data-table -->{"headers":["Metric","2024"],"rows":[["Revenue","£1.6M"]]}<!-- /C -->\n'
            '<!-- /SECTION -->'
        )
        html = assemble(marker_text, "classic", "Biz", "$1", "d")
        assert "£1.6M" in html
        assert "<table" in html

    def test_malformed_json_in_c_block_is_dropped_not_fatal(self, caplog):
        marker_text = (
            '<!-- SECTION num="I" title="X" -->\n'
            'before <!-- C:data-table -->not valid json<!-- /C --> after\n'
            '<!-- /SECTION -->'
        )
        html = assemble(marker_text, "classic", "Biz", "$1", "d")
        assert "before" in html
        assert "after" in html
        assert "<table" not in html

    def test_unknown_component_type_is_dropped_not_fatal(self):
        marker_text = (
            '<!-- SECTION num="I" title="X" -->\n'
            'before <!-- C:not-a-real-type -->{}<!-- /C --> after\n'
            '<!-- /SECTION -->'
        )
        html = assemble(marker_text, "classic", "Biz", "$1", "d")
        assert "before" in html
        assert "after" in html

    def test_non_component_prose_passes_through_untouched(self):
        marker_text = (
            '<!-- SECTION num="I" title="X" -->\n'
            "<p>Plain narrative prose, no components here.</p>\n"
            '<!-- /SECTION -->'
        )
        html = assemble(marker_text, "classic", "Biz", "$1", "d")
        assert "Plain narrative prose, no components here." in html

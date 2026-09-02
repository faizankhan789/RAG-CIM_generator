"""Tests for core/chrome/components.py — the fixed renderers for the 8
structural section-body components.

See core/chrome/components.py's own module docstring for the overlap-
prevention and anti-fabrication rationale locked in here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.chrome.components import (
    ComponentError,
    card_grid,
    chart_bar,
    chart_donut,
    data_table,
    icon,
    stat_strip,
    swot_grid,
    timeline,
    two_col,
)

PALETTE = {"primary": "#111111", "accent": "#c9a227", "light": "#fafafa", "mid": "#6b7280"}


def _no_absolute_position(html: str) -> bool:
    return "position:absolute" not in html and "position: absolute" not in html


class TestIcon:
    def test_known_icon_renders_svg(self):
        assert "<svg" in icon("dollar", "#111111")

    def test_unknown_icon_returns_empty(self):
        assert icon("not-a-real-icon", "#111111") == ""


class TestStatStrip:
    def test_renders_real_values(self):
        html = stat_strip(
            [{"icon": "dollar", "label": "Annual Revenue", "value": "$2,400,000"}], PALETTE
        )
        assert "$2,400,000" in html
        assert "Annual Revenue" in html
        assert _no_absolute_position(html)
        assert "min-width:0" in html

    def test_empty_payload_raises(self):
        with pytest.raises(ComponentError):
            stat_strip([], PALETTE)

    def test_missing_required_field_raises(self):
        with pytest.raises(ComponentError):
            stat_strip([{"icon": "dollar"}], PALETTE)


class TestDataTable:
    def test_renders_real_rows(self):
        html = data_table(
            {"headers": ["Metric", "2024"], "rows": [["Revenue", "£1.6M"]]}, PALETTE
        )
        assert "£1.6M" in html
        assert "<table" in html
        assert _no_absolute_position(html)

    def test_missing_headers_raises(self):
        with pytest.raises(ComponentError):
            data_table({"rows": [["a"]]}, PALETTE)


class TestChartBar:
    def test_renders_real_values(self):
        html = chart_bar(
            {"periods": [{"label": "2023", "value": 1200000}, {"label": "2024", "value": 1400000}]},
            PALETTE,
        )
        assert "<svg" in html
        assert "1.4M" in html or "1.2M" in html

    def test_caps_at_six_most_recent_periods(self):
        periods = [{"label": str(2010 + i), "value": 100 + i} for i in range(10)]
        html = chart_bar({"periods": periods}, PALETTE)
        # only the 6 most recent labels should appear
        for i in range(10):
            year = str(2010 + i)
            if i < 4:
                assert f">{year}<" not in html
            else:
                assert f">{year}<" in html

    def test_non_numeric_value_raises(self):
        with pytest.raises(ComponentError):
            chart_bar({"periods": [{"label": "2024", "value": "not-a-number"}]}, PALETTE)

    def test_empty_periods_raises(self):
        with pytest.raises(ComponentError):
            chart_bar({"periods": []}, PALETTE)


class TestChartDonut:
    def test_percentages_derive_from_raw_values_and_sum_to_100(self):
        html = chart_donut(
            {"segments": [{"label": "Rooms", "value": 750}, {"label": "F&B", "value": 250}]},
            PALETTE,
        )
        assert "75.0%" in html
        assert "25.0%" in html

    def test_zero_total_raises(self):
        with pytest.raises(ComponentError):
            chart_donut({"segments": [{"label": "A", "value": 0}]}, PALETTE)

    def test_negative_value_raises(self):
        with pytest.raises(ComponentError):
            chart_donut({"segments": [{"label": "A", "value": -5}, {"label": "B", "value": 10}]}, PALETTE)


class TestTwoCol:
    def test_renders_left_html_and_right_items(self):
        html = two_col(
            {"left_html": "<p>Real narrative.</p>", "right_items": ["Founded 2010"]}, PALETTE
        )
        assert "Real narrative." in html
        assert "Founded 2010" in html
        assert "min-width:0" in html
        assert _no_absolute_position(html)

    def test_missing_left_html_raises(self):
        with pytest.raises(ComponentError):
            two_col({"right_items": ["x"]}, PALETTE)


class TestCardGrid:
    def test_renders_real_cards(self):
        html = card_grid({"cols": 2, "cards": [{"title": "Jane Doe", "subtitle": "CEO"}]}, PALETTE)
        assert "Jane Doe" in html
        assert "CEO" in html
        assert "min-width:0" in html

    def test_empty_cards_raises(self):
        with pytest.raises(ComponentError):
            card_grid({"cols": 2, "cards": []}, PALETTE)


class TestSwotGrid:
    def test_renders_quadrants(self):
        html = swot_grid({"strengths": ["Strong brand"], "weaknesses": [], "opportunities": [], "threats": []}, PALETTE)
        assert "Strong brand" in html
        assert _no_absolute_position(html)

    def test_all_empty_raises(self):
        with pytest.raises(ComponentError):
            swot_grid({"strengths": [], "weaknesses": [], "opportunities": [], "threats": []}, PALETTE)


class TestTimeline:
    def test_renders_steps(self):
        html = timeline({"steps": [{"title": "Phase 1", "body": "Regional expansion"}]}, PALETTE)
        assert "Phase 1" in html
        assert "Regional expansion" in html

    def test_empty_steps_raises(self):
        with pytest.raises(ComponentError):
            timeline({"steps": []}, PALETTE)

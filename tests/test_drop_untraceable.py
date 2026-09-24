"""core.llm._drop_untraceable_figures — the template gives the design only; every
money figure / percentage in the CIM must come from the listing data. Anything the
source doesn't contain (the model invented or calculated it) is dropped.

Real run that motivated this: source had FY2024 EBITDA $3,180,000; the model moved it
to FY2023, invented "$3,565,000" for FY2024, and added a "9.4% CAGR" it computed."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from core.llm import _drop_untraceable_figures

SOURCE = """FY2023 revenue: $11,800,000. FY2025 revenue: $14,200,000.
FY2025 Adjusted EBITDA: $3,950,000 (27.8% margin), up from $3,180,000 in FY2024.
Occupancy: 92%. RevPAR: $186. Asking price $8.5M. F&B 34.0% of revenue."""


def _drop(html: str) -> str:
    return _drop_untraceable_figures(html, SOURCE)


# ── free HTML (both paths) ────────────────────────────────────────────────────

def test_real_figures_in_any_format_are_kept():
    html = ("<p>Revenue reached $14,200,000 with 92% occupancy, a 27.8% margin, "
            "RevPAR of $186 and F&B at 34% of revenue. Asking $8,500,000.</p>")
    assert _drop(html) == html


def test_sentence_with_invented_or_calculated_figure_is_dropped():
    html = ("<p>Revenue grew strongly. Three-year revenue CAGR of 9.4%, driven by F&B. "
            "EBITDA reached $3,950,000.</p>")
    out = _drop(html)
    assert "9.4%" not in out and "CAGR" not in out
    assert "Revenue grew strongly." in out
    assert "EBITDA reached $3,950,000." in out


def test_standalone_invented_value_becomes_dash():
    # table cell / KPI value / chart label: the figure is the whole text node
    html = "<tr><td>Adjusted EBITDA</td><td>$3,180,000</td><td>$3,565,000</td><td>$3,950,000</td></tr>"
    out = _drop(html)
    assert "$3,565,000" not in out
    assert "<td>—</td>" in out
    assert "<td>$3,180,000</td>" in out and "<td>$3,950,000</td>" in out


def test_list_item_that_is_only_an_invented_claim_is_removed():
    html = "<ul><li>Occupancy of 92%</li><li>EBITDA grew 24.1% year over year.</li></ul>"
    out = _drop(html)
    assert "24.1%" not in out
    assert "<li>Occupancy of 92%</li>" in out
    assert "<li></li>" not in out


def test_style_blocks_and_attributes_untouched():
    html = '<style>.bar{width:37%}</style><div style="width:63%" title="$9,999"><p>92% occupancy</p></div>'
    assert _drop(html) == html


def test_no_source_figures_at_all_does_not_wipe_numbers_from_empty_source():
    # With an empty source nothing is traceable — the guard still only touches figures.
    out = _drop_untraceable_figures("<p>Founded in 2011 by two partners.</p>", "")
    assert out == "<p>Founded in 2011 by two partners.</p>"   # years are not money/percent


# ── built-in path: component JSON blocks ───────────────────────────────────────

def _block(c_type: str, payload) -> str:
    return f"<!-- C:{c_type} -->\n{json.dumps(payload)}\n<!-- /C -->"


def _payload(out: str, c_type: str):
    m = re.search(rf"<!--\s*C:{c_type}\s*-->(.*?)<!--\s*/C\s*-->", out, re.DOTALL)
    return json.loads(m.group(1))


def test_stat_strip_card_with_invented_value_is_dropped():
    out = _drop(_block("stat-strip", [
        {"label": "Revenue", "value": "$14,200,000"},
        {"label": "Revenue CAGR", "value": "9.4%"},
    ]))
    assert [c["label"] for c in _payload(out, "stat-strip")] == ["Revenue"]


def test_data_table_invented_cell_becomes_dash_and_json_stays_valid():
    out = _drop(_block("data-table", {
        "headers": ["Metric", "FY2023", "FY2024", "FY2025"],
        "rows": [["Adjusted EBITDA", "$3,180,000", "$3,565,000", "$3,950,000"]],
    }))
    assert _payload(out, "data-table")["rows"] == [["Adjusted EBITDA", "$3,180,000", "—", "$3,950,000"]]


def test_chart_bar_period_with_invented_raw_value_is_dropped():
    out = _drop(_block("chart-bar", {"periods": [
        {"label": "FY2023", "value": 11800000},
        {"label": "FY2024", "value": 12900000},   # not in source
        {"label": "FY2025", "value": 14200000},
    ], "currency": "$"}))
    assert [p["label"] for p in _payload(out, "chart-bar")["periods"]] == ["FY2023", "FY2025"]


def test_prose_inside_component_json_drops_invented_sentence():
    out = _drop(_block("two-col", {
        "left_html": "<p>Strong operator. Margins expanded 3.1 points to 27.8%.</p>",
        "right_items": ["Occupancy 92%", "CAGR 9.4%"],
    }))
    payload = _payload(out, "two-col")
    assert "Strong operator." in payload["left_html"]
    assert payload["right_items"] == ["Occupancy 92%"]


def test_malformed_component_json_is_left_untouched():
    html = "<!-- C:data-table -->\n{not json $3,565,000\n<!-- /C -->"
    assert _drop(html) == html


# ── low temperature for factual output, only where the model accepts it ──────

import pytest
from unittest.mock import patch


@pytest.mark.parametrize("model, sent", [
    ("claude-haiku-4-5-20251001", True),
    ("claude-sonnet-4-6", True),
    ("claude-opus-4-6", True),
    ("claude-opus-5", False),        # sampling params removed -> would 400
    ("claude-opus-4-8", False),
    ("claude-sonnet-5", False),
    ("claude-fable-5-1", False),
    ("some-future-model", False),    # unknown -> never risk a 400
])
def test_temperature_only_sent_to_models_that_accept_it(model, sent):
    from core import llm
    with patch.object(llm, "MODEL", model):
        kwargs = llm._sampling_kwargs(0.2)
    assert kwargs == ({"temperature": 0.2} if sent else {})

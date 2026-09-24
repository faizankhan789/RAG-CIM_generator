"""core/facts.py — verified facts table (metric | period | value) and the
template-agnostic enforcement that keeps every figure in its right place.
See docs/superpowers/specs/2026-09-24-verified-facts-design.md."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from core.facts import build_facts, enforce_facts, format_facts, period_key, verify_facts

SOURCE = """## III. Financial Information
- FY2023 revenue: $11,800,000. FY2024 revenue: $12,900,000. FY2025 revenue: $14,200,000.
- FY2025 Adjusted EBITDA: $3,950,000 (27.8% margin), up from $3,180,000 in FY2024.
- Marketing mix: direct (38% of bookings), repeat/referral (27%).

| Metric | 2022 | 2023 |
|---|---|---|
| Gross Profit | $900,000 | $1,050,000 |
"""

FACTS = [
    {"metric": "Revenue", "period": "FY2023", "value": "$11,800,000"},
    {"metric": "Revenue", "period": "FY2024", "value": "$12,900,000"},
    {"metric": "Revenue", "period": "FY2025", "value": "$14,200,000"},
    {"metric": "Adjusted EBITDA", "period": "FY2025", "value": "$3,950,000"},
    {"metric": "Adjusted EBITDA", "period": "FY2024", "value": "$3,180,000"},
    {"metric": "Adjusted EBITDA margin", "period": "FY2025", "value": "27.8%"},
    {"metric": "Repeat/referral share of bookings", "period": "", "value": "27%"},
]


# ── period normalization ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text, key", [
    ("FY2024", "2024"), ("2024", "2024"), ("FY 2024", "2024"), ("CY2023", "2023"),
    ("Q1 2024", "Q1 2024"), ("Q3-2023", "Q3 2023"), ("TTM", "TTM"), ("LTM", "TTM"),
    ("YTD 2025", "YTD 2025"), ("Metric", None), ("Adjusted EBITDA", None),
])
def test_period_key(text, key):
    assert period_key(text) == key


# ── verification ──────────────────────────────────────────────────────────────

def test_correct_facts_survive_verification():
    assert len(verify_facts(FACTS, SOURCE)) == len(FACTS)


def test_fact_with_wrong_period_is_rejected():
    wrong = [{"metric": "Adjusted EBITDA", "period": "FY2023", "value": "$3,180,000"},
             {"metric": "Revenue", "period": "FY2023", "value": "$12,900,000"}]   # same line, other sentence
    assert verify_facts(wrong, SOURCE) == []


def test_fact_whose_value_is_not_in_source_is_rejected():
    assert verify_facts([{"metric": "Revenue", "period": "FY2024", "value": "$12,950,000"}], SOURCE) == []


def test_markdown_table_facts_in_source_verify_via_header_period():
    ok = [{"metric": "Gross Profit", "period": "2023", "value": "$1,050,000"}]
    bad = [{"metric": "Gross Profit", "period": "2022", "value": "$1,050,000"}]
    assert len(verify_facts(ok, SOURCE)) == 1
    assert verify_facts(bad, SOURCE) == []


def test_malformed_fact_rows_are_ignored():
    junk = ["x", {"metric": "", "value": "$1"}, {"metric": "Revenue"}, {"metric": "Revenue", "value": "n/a"}]
    assert verify_facts(junk, SOURCE) == []


def test_format_facts_lists_each_fact():
    text = format_facts(verify_facts(FACTS, SOURCE))
    assert "Adjusted EBITDA | FY2024 | $3,180,000" in text


# ── enforcement: HTML tables (any orientation) ────────────────────────────────

def _cells(html: str) -> list[list[str]]:
    rows = re.findall(r"<tr.*?</tr>", html, re.S)
    return [[re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<t[hd][^>]*>.*?</t[hd]>", r, re.S)] for r in rows]


def test_misplaced_cell_in_period_columns_becomes_dash():
    html = ("<table><tr><th>Metric</th><th>FY2023</th><th>FY2024</th><th>FY2025</th></tr>"
            "<tr><td>Adjusted EBITDA</td><td>$3,180,000</td><td>$3,180,000</td><td>$3,950,000</td></tr>"
            "<tr><td>Adjusted EBITDA Margin</td><td>27.0%</td><td>—</td><td>27.8%</td></tr>"
            "<tr><td>Total Revenue</td><td>$11,800,000</td><td>$12,900,000</td><td>$14,200,000</td></tr></table>")
    rows = _cells(enforce_facts(html, FACTS))
    assert rows[1] == ["Adjusted EBITDA", "—", "$3,180,000", "$3,950,000"]
    assert rows[2] == ["Adjusted EBITDA Margin", "—", "—", "27.8%"]
    assert rows[3] == ["Total Revenue", "$11,800,000", "$12,900,000", "$14,200,000"]


def test_periods_as_rows_orientation():
    html = ("<table><tr><th>Year</th><th>Revenue</th><th>Adjusted EBITDA</th></tr>"
            "<tr><td>FY2023</td><td>$11,800,000</td><td>$3,180,000</td></tr>"
            "<tr><td>FY2024</td><td>$12,900,000</td><td>$3,180,000</td></tr></table>")
    rows = _cells(enforce_facts(html, FACTS))
    assert rows[1] == ["FY2023", "$11,800,000", "—"]
    assert rows[2] == ["FY2024", "$12,900,000", "$3,180,000"]


def test_row_left_all_dash_is_removed():
    html = ("<table><tr><th>Metric</th><th>FY2023</th><th>FY2024</th></tr>"
            "<tr><td>Adjusted EBITDA</td><td>$3,950,000</td><td>$3,950,000</td></tr>"
            "<tr><td>Revenue</td><td>$11,800,000</td><td>$12,900,000</td></tr></table>")
    out = enforce_facts(html, FACTS)
    assert "Adjusted EBITDA" not in out
    assert "$12,900,000" in out


def test_table_without_period_axis_is_untouched():
    html = "<table><tr><th>Property</th><th>Keys</th></tr><tr><td>Naples</td><td>70</td></tr></table>"
    assert enforce_facts(html, FACTS) == html


def test_table_with_spans_is_skipped():
    html = ('<table><tr><th colspan="2">FY2023</th></tr>'
            "<tr><td>Adjusted EBITDA</td><td>$3,180,000</td></tr></table>")
    assert enforce_facts(html, FACTS) == html


def test_only_tables_are_reserialized_rest_of_document_byte_identical():
    head = '<!DOCTYPE html><html><body><svg viewBox="0 0 24 24"><polyline points="4 12"/></svg>'
    table = ("<table><tr><th>Metric</th><th>FY2023</th></tr>"
             "<tr><td>Adjusted EBITDA</td><td>$3,180,000</td></tr>"
             "<tr><td>Revenue</td><td>$11,800,000</td></tr></table>")
    out = enforce_facts(head + table + "</body></html>", FACTS)
    assert out.startswith(head)
    assert out.endswith("</body></html>")


# ── enforcement: built-in component JSON ──────────────────────────────────────

def _block(c_type, payload):
    return f"<!-- C:{c_type} -->\n{json.dumps(payload)}\n<!-- /C -->"


def _payload(out, c_type):
    return json.loads(re.search(rf"<!--\s*C:{c_type}\s*-->(.*?)<!--\s*/C\s*-->", out, re.S).group(1))


def test_component_data_table_misplaced_cell():
    out = enforce_facts(_block("data-table", {
        "headers": ["Metric", "FY2023", "FY2024", "FY2025"],
        "rows": [["Adjusted EBITDA", "$3,180,000", "$3,180,000", "$3,950,000"]],
    }), FACTS)
    # FY2023 had only the misplaced value -> blanked -> a column with no data is dropped
    assert _payload(out, "data-table") == {"headers": ["Metric", "FY2024", "FY2025"],
                                            "rows": [["Adjusted EBITDA", "$3,180,000", "$3,950,000"]]}


def test_component_data_table_keeps_column_that_still_has_data():
    out = enforce_facts(_block("data-table", {
        "headers": ["Metric", "FY2023", "FY2024"],
        "rows": [["Adjusted EBITDA", "$3,180,000", "$3,180,000"], ["Revenue", "$11,800,000", "$12,900,000"]],
    }), FACTS)
    assert _payload(out, "data-table")["rows"] == [["Adjusted EBITDA", "—", "$3,180,000"],
                                                   ["Revenue", "$11,800,000", "$12,900,000"]]


def test_component_chart_bar_period_with_misplaced_value_dropped():
    out = enforce_facts(_block("chart-bar", {"periods": [
        {"label": "FY2023", "value": 3180000}, {"label": "FY2024", "value": 3180000},
        {"label": "FY2025", "value": 3950000}]}), FACTS)
    assert [p["label"] for p in _payload(out, "chart-bar")["periods"]] == ["FY2024", "FY2025"]


def test_component_stat_card_with_wrong_period_in_label_dropped():
    out = enforce_facts(_block("stat-strip", [
        {"label": "FY2023 Adjusted EBITDA", "value": "$3,180,000"},
        {"label": "FY2025 Revenue", "value": "$14,200,000"}]), FACTS)
    assert [c["label"] for c in _payload(out, "stat-strip")] == ["FY2025 Revenue"]


# ── enforcement: prose ────────────────────────────────────────────────────────

def test_sentence_tying_figure_to_wrong_period_is_dropped():
    html = "<p>In FY2023, EBITDA was $3,180,000. Revenue reached $14,200,000 in FY2025.</p>"
    out = enforce_facts(html, FACTS)
    assert "$3,180,000" not in out
    assert "Revenue reached $14,200,000 in FY2025." in out


def test_source_style_sentence_with_two_periods_is_kept():
    html = "<p>FY2025 Adjusted EBITDA was $3,950,000, up from $3,180,000 in FY2024.</p>"
    assert enforce_facts(html, FACTS) == html


def test_no_facts_means_no_enforcement():
    html = "<p>In FY2023, EBITDA was $3,180,000.</p>"
    assert enforce_facts(html, []) == html


# ── build_facts (LLM call mocked) ─────────────────────────────────────────────

def _client_returning(text):
    client = MagicMock()
    msg = MagicMock(content=[MagicMock(text=text)], usage=MagicMock(input_tokens=5, output_tokens=5),
                    stop_reason="end_turn")
    client.messages.create = AsyncMock(return_value=msg)
    return client


@pytest.mark.asyncio
async def test_build_facts_returns_only_verified_rows():
    reply = json.dumps({"facts": FACTS + [{"metric": "Adjusted EBITDA", "period": "FY2023", "value": "$3,180,000"}]})
    with patch("core.llm.get_client", return_value=_client_returning(reply)):
        facts = await build_facts("", [SOURCE])
    assert len(facts) == len(FACTS)
    assert not any(f["period"] == "FY2023" and f["metric"] == "Adjusted EBITDA" for f in facts)


@pytest.mark.asyncio
async def test_build_facts_failure_returns_empty_list():
    with patch("core.llm.get_client", return_value=_client_returning("not json")):
        assert await build_facts("", [SOURCE]) == []


# ── pipeline wiring ───────────────────────────────────────────────────────────

def _stream_client(reply: str, captured: dict):
    client = MagicMock()
    final_msg = MagicMock(content=[MagicMock(text=reply)],
                          usage=MagicMock(input_tokens=1, output_tokens=1), stop_reason="end_turn")
    stream_cm = MagicMock()
    stream_cm.__aenter__ = AsyncMock(return_value=MagicMock(get_final_message=AsyncMock(return_value=final_msg)))
    stream_cm.__aexit__ = AsyncMock(return_value=False)

    def _stream(**kw):
        captured["messages"] = kw["messages"]
        return stream_cm
    client.messages.stream = _stream
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("custom", [True, False])
async def test_generate_cim_html_gets_facts_and_enforces_them(custom):
    from core.llm import generate_cim_html
    table = ("<table><tr><th>Metric</th><th>FY2023</th><th>FY2024</th></tr>"
             "<tr><td>Adjusted EBITDA</td><td>$3,180,000</td><td>$3,180,000</td></tr>"
             "<tr><td>Revenue</td><td>$11,800,000</td><td>$12,900,000</td></tr></table>")
    reply = f"<!DOCTYPE html><html><body>{table}</body></html>" if custom else table
    captured: dict = {}
    with patch("core.llm.get_client", return_value=_stream_client(reply, captured)):
        out = await generate_cim_html(
            all_findings=[SOURCE], listing_xml="", listing_name="Acme", asking_price="",
            template_id="minimalist",
            custom_template={"name": "T", "allow_brand_override": False, "palette": None} if custom else None,
            facts=FACTS,
        )
    prompt = " ".join(b.get("text", "") for b in captured["messages"][0]["content"])
    assert "VERIFIED FINANCIAL FACTS" in prompt
    assert "Adjusted EBITDA | FY2024 | $3,180,000" in prompt
    assert "<td>—</td><td>$3,180,000</td>" in out


@pytest.mark.asyncio
async def test_formatter_builds_facts_and_passes_them_to_generation():
    from nodes import formatter
    gen = AsyncMock(return_value="<!DOCTYPE html><html></html>")
    with patch.object(formatter, "build_facts", AsyncMock(return_value=FACTS)) as bf, \
         patch.object(formatter, "generate_cim_html", gen):
        await formatter.formatter_node({
            "all_findings": [SOURCE], "listing_xml": "<x/>", "listing_name": "Acme",
            "custom_template": {"name": "T"},
        })
    bf.assert_awaited_once_with("<x/>", [SOURCE])
    assert gen.call_args.kwargs["facts"] == FACTS


@pytest.mark.asyncio
async def test_build_facts_call_has_its_own_timeout():
    from core.facts import _FACTS_TIMEOUT_SECONDS
    client = _client_returning(json.dumps({"facts": FACTS}))
    with patch("core.llm.get_client", return_value=client):
        await build_facts("", [SOURCE])
    assert client.messages.create.call_args.kwargs["timeout"] == _FACTS_TIMEOUT_SECONDS <= 120


@pytest.mark.asyncio
async def test_build_facts_timeout_falls_back_to_no_facts():
    import anthropic
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=anthropic.APITimeoutError(request=MagicMock()))
    with patch("core.llm.get_client", return_value=client):
        assert await build_facts("", [SOURCE]) == []

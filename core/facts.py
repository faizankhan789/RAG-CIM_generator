"""Verified facts — keeps every figure in a CIM in its right place.

The design template (built-in or uploaded — it can be anything) supplies design
only; every figure comes from the listing data. Findings reach the generator as
prose, and the model sometimes puts a real number under the wrong metric or year
(real run: FY2024 EBITDA $3,180,000 also shown as FY2023). This module:

1. build_facts   — one Claude call turns listing XML + findings into
                   [{metric, period, value}], then verify_facts keeps only rows whose
                   value is tied to that period somewhere in the source text.
2. format_facts  — the verified table, handed to the generator as the only allowed
                   source of figures for tables/KPIs/charts.
3. enforce_facts — after generation, template-agnostic: table cells (HTML tables and
                   built-in component JSON, periods as columns OR rows), chart periods,
                   period-labelled KPI cards, and prose sentences whose figure sits in
                   a period the facts don't support are blanked ("—") or dropped.

See docs/superpowers/specs/2026-09-24-verified-facts-design.md.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any

log = logging.getLogger(__name__)

DASH = "—"
_DASH_LIKE = {"", "—", "–", "-", "n/a", "na", "n.a.", "nm"}

# ── periods ───────────────────────────────────────────────────────────────────

_PERIOD_RE = re.compile(
    r"(?P<q>\b[QH][1-4])\s*[-/ ]?\s*(?:FY|CY)?\s*'?(?P<qy>(?:19|20)\d{2})\b"
    r"|\b(?P<ytd>YTD)\s*(?:FY|CY)?\s*(?P<ytdy>(?:19|20)\d{2})?\b"
    r"|\b(?P<ttm>TTM|LTM)\b"
    r"|(?<![\d,.$£€])\b(?:FYE?|CY)?\s?'?(?P<y>(?:19|20)\d{2})\b(?![,.]\d)"
    r"|\bFY\s?'?(?P<y2>\d{2})\b",
    re.IGNORECASE,
)


def _key_from_match(m: re.Match) -> str:
    if m.group("q"):
        return f"{m.group('q').upper()} {m.group('qy')}"
    if m.group("ytd"):
        return f"YTD {m.group('ytdy')}" if m.group("ytdy") else "YTD"
    if m.group("ttm"):
        return "TTM"
    if m.group("y"):
        return m.group("y")
    return f"20{m.group('y2')}"


def find_periods(text: str) -> list[tuple[str, int, int]]:
    """Every period mention in text: (normalized key, start, end)."""
    return [(_key_from_match(m), m.start(), m.end()) for m in _PERIOD_RE.finditer(text)]


def period_key(text: str) -> str | None:
    """The one period a short label names (FY2024 -> "2024", "Q1 2024", "TTM"), else None."""
    keys = {k for k, _s, _e in find_periods(text or "")}
    return keys.pop() if len(keys) == 1 else None


# ── numbers ───────────────────────────────────────────────────────────────────

_UNIT_POW = {"k": 3, "thousand": 3, "m": 6, "mm": 6, "million": 6, "b": 9, "bn": 9, "billion": 9}
_NUMBER_RE = re.compile(
    r"(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s?(?P<unit>(?i:thousand|million|billion|bn)|MM|[kKmMbB])(?![A-Za-z]))?"
)


def _to_decimal(num: str, unit: str | None = None) -> Decimal | None:
    try:
        value = Decimal(num.replace(",", ""))
    except InvalidOperation:
        return None
    return value.scaleb(_UNIT_POW[unit.lower()]) if unit else value


def find_figures(text: str) -> list[tuple[Decimal, int, int]]:
    """Every number in text that isn't part of a period mention: (value, start, end)."""
    period_spans = [(s, e) for _k, s, e in find_periods(text)]
    out = []
    for m in _NUMBER_RE.finditer(text):
        if any(s <= m.start() < e for s, e in period_spans):
            continue
        value = _to_decimal(m.group("num"), m.group("unit"))
        if value is not None:
            out.append((value, m.start(), m.end()))
    return out


def _value_of(raw: Any) -> Decimal | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    if isinstance(raw, str):
        figures = find_figures(raw)
        return figures[0][0] if figures else None
    return None


# ── figure <-> period association ─────────────────────────────────────────────

_RIGHT_PERIOD_RE = re.compile(
    r"^\s*(?:\)|,)?\s*(?:in|for|during|as of|at|of)?\s*(?:the\s+)?(?:fiscal\s+)?(?:year\s+)?$",
    re.IGNORECASE,
)


def _associated_period(text: str, fig_start: int, fig_end: int,
                       periods: list[tuple[str, int, int]], figures: list[tuple[Decimal, int, int]],
                       strong_only: bool) -> str | None:
    """The period a figure is stated for, by reading order:
    1. a period right after it ("$3,180,000 in FY2024"),
    2. else the nearest period before it — strong_only requires no other figure in between,
    3. else (not strong_only) the nearest period after it."""
    for key, s, _e in periods:
        if s >= fig_end and s - fig_end <= 25 and _RIGHT_PERIOD_RE.match(text[fig_end:s]):
            return key

    def figure_between(a: int, b: int) -> bool:
        return any(a <= fs and fe <= b for _v, fs, fe in figures if (fs, fe) != (fig_start, fig_end))

    before = [(key, s, e) for key, s, e in periods if e <= fig_start]
    if before:
        key, _s, e = before[-1]
        if not (strong_only and figure_between(e, fig_start)):
            return key
    if strong_only:
        return None
    after = [(key, s, e) for key, s, e in periods if s >= fig_end]
    return after[0][0] if after else None


# ── verification ──────────────────────────────────────────────────────────────

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+")


def _source_segments(source_text: str) -> list[str]:
    """Sentences of the source, plus one synthetic "metric period value" line per
    cell of any markdown table (the period lives in the table's header row)."""
    segments: list[str] = []
    header: list[str] | None = None
    for line in source_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue                                  # |---|---| separator
            if header is None and sum(period_key(c) is not None for c in cells[1:]) >= 1:
                header = cells
                continue
            if header is not None:
                for j, cell in enumerate(cells[1:], start=1):
                    if j < len(header) and header[j]:
                        segments.append(f"{cells[0]} {header[j]} {cell}")
                continue
            segments.append(" ".join(cells))
            continue
        header = None
        segments.extend(s for s in _SENTENCE_SPLIT_RE.split(stripped) if s)
    return segments


def verify_facts(raw_facts: Any, source_text: str) -> list[dict]:
    """Keep only well-formed facts whose value appears in the source AND, when the
    fact names a period, is stated for that period there (same sentence, by reading
    order) — a real number claimed for the wrong year is rejected."""
    if not isinstance(raw_facts, list):
        return []
    segments = [(seg, find_periods(seg), find_figures(seg)) for seg in _source_segments(source_text)]
    verified = []
    for fact in raw_facts:
        if not isinstance(fact, dict):
            continue
        metric = fact.get("metric")
        value = _value_of(fact.get("value"))
        period = fact.get("period") or ""
        if not isinstance(metric, str) or not metric.strip() or value is None or not isinstance(period, str):
            continue
        pk = period_key(period) if period.strip() else None
        for seg, periods, figures in segments:
            spots = [(s, e) for v, s, e in figures if v == value]
            if not spots:
                continue
            if pk is None or any(
                _associated_period(seg, s, e, periods, figures, strong_only=False) == pk for s, e in spots
            ):
                verified.append({"metric": metric.strip(), "period": period.strip(), "value": str(fact["value"]).strip()})
                break
    return verified


def format_facts(facts: list[dict]) -> str:
    return "\n".join(f"- {f['metric']} | {f['period'] or '(no period)'} | {f['value']}" for f in facts)


# ── building the facts table (one Claude call) ────────────────────────────────

_FACTS_PROMPT = """\
List EVERY figure stated in the source data above — money amounts, percentages, counts,
ratios, ratings — as one JSON object, no markdown fences, no explanation:

{"facts": [{"metric": "short name of what the figure measures, e.g. Revenue / Adjusted EBITDA / EBITDA margin / Occupancy / Asking price / Employees",
            "period": "the period the source states it for, exactly as written (FY2024, 2023, Q1 2024, TTM), or \\"\\" if the source gives none",
            "value": "the figure exactly as written in the source"}]}

Rules:
- Only figures the source explicitly states. Never calculate, estimate, convert or combine.
- One entry per metric + period. The period must be the one the source ties to that exact
  figure — if a sentence says "$3,950,000, up from $3,180,000 in FY2024", then $3,180,000
  is FY2024 and $3,950,000 is whatever period the source gives it (or "").
- Use the same metric name every time the same measure appears in different periods.
"""


# Own timeout instead of the SDK's 600 s default (x3 with retries): a hung call would
# stall the whole CIM generation. On timeout build_facts returns [] and generation
# proceeds exactly as before, just without facts enforcement.
_FACTS_TIMEOUT_SECONDS = 90


async def build_facts(listing_xml: str, findings: list[str]) -> list[dict]:
    """One Claude call (temperature 0) -> verified facts. Returns [] on any failure —
    generation then proceeds exactly as before, without facts enforcement."""
    from core import llm   # lazy: core.llm imports this module

    source_text = "\n".join([listing_xml or "", *findings])
    if not source_text.strip():
        return []
    try:
        response = await llm.get_client().messages.create(
            model=llm.MODEL,
            max_tokens=16000,
            timeout=_FACTS_TIMEOUT_SECONDS,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "## Source data\n\n" + source_text},
                {"type": "text", "text": _FACTS_PROMPT},
            ]}],
            **llm._sampling_kwargs(0.0),
        )
        llm._add_tokens(response.usage.input_tokens, response.usage.output_tokens)
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
        parsed = json.loads(raw)
    except Exception as exc:
        log.error("Facts: building the facts table failed: %s", exc)
        return []
    raw_facts = parsed.get("facts") if isinstance(parsed, dict) else None
    facts = verify_facts(raw_facts, source_text)
    rejected = (len(raw_facts) if isinstance(raw_facts, list) else 0) - len(facts)
    log.info("Facts: %d verified fact(s), %d rejected (value/period not tied in source)", len(facts), rejected)
    return facts


# ── enforcement ───────────────────────────────────────────────────────────────

_STOPWORDS = {"total", "the", "of", "and", "net", "annual", "fy", "cy", "year", "value", "amount"}


def _metric_tokens(text: str) -> frozenset[str]:
    no_periods = _PERIOD_RE.sub(" ", text or "")
    return frozenset(t for t in re.findall(r"[a-z]+", no_periods.lower()) if t not in _STOPWORDS)


class _FactIndex:
    def __init__(self, facts: list[dict]):
        self.rows = []   # (metric_tokens, period_key, value)
        for f in facts:
            value = _value_of(f.get("value"))
            if value is None or not isinstance(f.get("metric"), str):
                continue
            pk = period_key(f.get("period") or "") if (f.get("period") or "").strip() else None
            self.rows.append((_metric_tokens(f["metric"]), pk, value))
        self.metrics = {m for m, _p, _v in self.rows if m}
        self.period_keys = {p for _m, p, _v in self.rows if p}

    def __bool__(self) -> bool:
        return bool(self.rows)

    def metric_for(self, label: str) -> frozenset[str] | None:
        tokens = _metric_tokens(label)
        if not tokens:
            return None
        best, score = None, 0.0
        for metric in self.metrics:
            s = len(tokens & metric) / len(tokens | metric)
            if s > score:
                best, score = metric, s
        return best if score >= 0.5 else None

    def cell_ok(self, value: Decimal, period: str, metric: frozenset[str] | None) -> bool:
        if metric is not None:
            dated = [(p, v) for m, p, v in self.rows if m == metric and p]
            if not dated:
                return True                               # metric has no periods: can't judge
            return any(p == period and v == value for p, v in dated)
        # Unknown metric: only flag a value the facts place in other periods only.
        periods = {p for _m, p, v in self.rows if v == value and p}
        return not periods or period in periods

    def periods_of(self, value: Decimal) -> set[str]:
        return {p for _m, p, v in self.rows if v == value and p}


def _grid_edits(grid: list[list[str]], index: _FactIndex) -> tuple[set[tuple[int, int]], set[int], set[int]]:
    """For a table (grid[0] = header row): cells to blank, rows and columns to drop."""
    if len(grid) < 2:
        return set(), set(), set()
    width = max(len(r) for r in grid)
    col_periods = {c: period_key(grid[0][c]) for c in range(1, len(grid[0]))}
    row_periods = {r: period_key(grid[r][0]) for r in range(1, len(grid)) if grid[r]}
    if sum(p is not None for p in col_periods.values()) >= 2:
        def locate(r, c):
            return col_periods.get(c), index.metric_for(grid[r][0])
    elif sum(p is not None for p in row_periods.values()) >= 2:
        def locate(r, c):
            return row_periods.get(r), index.metric_for(grid[0][c] if c < len(grid[0]) else "")
    else:
        return set(), set(), set()

    dash: set[tuple[int, int]] = set()
    for r in range(1, len(grid)):
        for c in range(1, len(grid[r])):
            period, metric = locate(r, c)
            if not period:
                continue
            figures = find_figures(grid[r][c])
            if figures and not all(index.cell_ok(v, period, metric) for v, _s, _e in figures):
                dash.add((r, c))

    def empty(r, c):
        return (r, c) in dash or c >= len(grid[r]) or grid[r][c].strip().lower() in _DASH_LIKE

    drop_rows = {r for r in range(1, len(grid)) if len(grid[r]) > 1 and all(empty(r, c) for c in range(1, len(grid[r])))}
    kept = [r for r in range(1, len(grid)) if r not in drop_rows]
    drop_cols = {c for c in range(1, width) if kept and all(empty(r, c) for r in kept)}
    return dash, drop_rows, drop_cols


class _TableScanner(HTMLParser):
    """Locates rows/cells of ONE simple table by character offset (no re-serialization).
    Marks the table unsupported on nested tables, row/col spans or unclosed cells."""

    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.text = text
        self.line_starts = [0] + [m.end() for m in re.finditer(r"\n", text)]
        self.rows: list[dict] = []
        self.depth = 0
        self.cell: dict | None = None
        self.supported = True

    def _offset(self) -> int:
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        start = self._offset()
        if tag == "table":
            self.depth += 1
            if self.depth > 1:
                self.supported = False
        elif tag == "tr" and self.depth == 1:
            self.rows.append({"start": start, "cells": []})
        elif tag in ("td", "th") and self.depth == 1:
            if self.cell is not None or not self.rows or any(
                k in ("colspan", "rowspan") and (v or "1").strip() not in ("", "1") for k, v in attrs
            ):
                self.supported = False
                return
            self.cell = {"start": start, "inner": start + len(self.get_starttag_text())}

    def handle_endtag(self, tag):
        pos = self._offset()
        if tag == "table":
            self.depth -= 1
        elif tag in ("td", "th") and self.depth == 1 and self.cell is not None:
            self.cell["inner_end"] = pos
            self.cell["end"] = self.text.index(">", pos) + 1
            self.rows[-1]["cells"].append(self.cell)
            self.cell = None
        elif tag == "tr" and self.depth == 1 and self.rows:
            self.rows[-1]["end"] = self.text.index(">", pos) + 1


def _cell_text(inner_html: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", "", inner_html)).strip()


def _enforce_html_table(table: str, index: _FactIndex) -> str:
    scanner = _TableScanner(table)
    try:
        scanner.feed(table)
        scanner.close()
    except Exception:
        return table
    rows = [r for r in scanner.rows if r["cells"]]
    if not scanner.supported or scanner.cell is not None or any("end" not in r for r in rows) or len(rows) < 2:
        return table
    grid = [[_cell_text(table[c["inner"]:c["inner_end"]]) for c in r["cells"]] for r in rows]
    dash, drop_rows, drop_cols = _grid_edits(grid, index)
    if not (dash or drop_rows or drop_cols):
        return table

    edits: list[tuple[int, int, str]] = []   # (start, end, replacement) on the table string
    for r, row in enumerate(rows):
        if r in drop_rows:
            edits.append((row["start"], row["end"], ""))
            continue
        for c, cell in enumerate(row["cells"]):
            if c in drop_cols:
                edits.append((cell["start"], cell["end"], ""))
            elif (r, c) in dash:
                edits.append((cell["inner"], cell["inner_end"], DASH))
    for start, end, repl in sorted(edits, reverse=True):
        table = table[:start] + repl + table[end:]
    return table


def _enforce_component(c_type: str, payload: Any, index: _FactIndex) -> Any:
    if c_type == "data-table" and isinstance(payload, dict):
        headers, rows = payload.get("headers"), payload.get("rows")
        if not (isinstance(headers, list) and isinstance(rows, list) and all(isinstance(r, list) for r in rows)):
            return payload
        grid = [[str(h) for h in headers]] + [[str(c) for c in r] for r in rows]
        dash, drop_rows, drop_cols = _grid_edits(grid, index)
        if not (dash or drop_rows or drop_cols):
            return payload
        new_rows = [[DASH if (r, c) in dash else cell for c, cell in enumerate(row) if c not in drop_cols]
                    for r, row in enumerate(rows, start=1) if r not in drop_rows]
        return {**payload, "headers": [h for c, h in enumerate(headers) if c not in drop_cols], "rows": new_rows}

    if c_type == "chart-bar" and isinstance(payload, dict) and isinstance(payload.get("periods"), list):
        kept = []
        for p in payload["periods"]:
            period = period_key(str(p.get("label", ""))) if isinstance(p, dict) else None
            value = _value_of(p.get("value")) if isinstance(p, dict) else None
            if period and value is not None and not index.cell_ok(value, period, None):
                continue
            kept.append(p)
        return {**payload, "periods": kept}

    if c_type == "stat-strip" and isinstance(payload, list):
        kept = []
        for card in payload:
            if isinstance(card, dict):
                label = str(card.get("label", ""))
                period, value = period_key(label), _value_of(card.get("value"))
                if period and value is not None and not index.cell_ok(value, period, index.metric_for(label)):
                    continue
            kept.append(card)
        return kept
    return payload


_TABLE_RE = re.compile(r"<table\b.*?</table\s*>", re.IGNORECASE | re.DOTALL)
_COMPONENT_RE = re.compile(r"(<!--\s*C:([\w-]+)\s*-->)(.*?)(<!--\s*/C(?::[\w-]+)?\s*-->)", re.DOTALL)
_STYLE_OR_SCRIPT_RE = re.compile(r"<(style|script)\b.*?</\1>", re.IGNORECASE | re.DOTALL)


def _enforce_prose(html: str, index: _FactIndex) -> str:
    """Drop a sentence that states a figure for a period the facts don't support
    (strong reading-order association only; years that aren't fact periods — e.g. a
    founding year — are ignored)."""
    def fix_text(m: re.Match) -> str:
        text = m.group(1)
        sentences = _SENTENCE_SPLIT_RE.split(text)
        if len(sentences) == 1 and not text.strip():
            return m.group(0)
        kept = []
        for sent in sentences:
            periods = [p for p in find_periods(sent) if p[0] in index.period_keys]
            figures = find_figures(sent)
            bad = False
            for value, s, e in figures:
                known = index.periods_of(value)
                if not known:
                    continue
                said = _associated_period(sent, s, e, periods, figures, strong_only=True)
                if said and said not in known:
                    bad = True
                    break
            if not bad:
                kept.append(sent)
        if len(kept) == len(sentences):
            return m.group(0)
        return ">" + " ".join(kept) + "<"

    out, last = [], 0
    for block in _STYLE_OR_SCRIPT_RE.finditer(html):
        out.append(re.sub(r">([^<]+)<", fix_text, html[last:block.start()]))
        out.append(block.group(0))
        last = block.end()
    out.append(re.sub(r">([^<]+)<", fix_text, html[last:]))
    return "".join(out)


def enforce_facts(html: str, facts: list[dict]) -> str:
    """Template-agnostic placement check of a generated CIM (full HTML or the
    built-in path's marker text) against the verified facts. No facts -> unchanged."""
    index = _FactIndex(facts or [])
    if not index:
        return html

    def fix_component(m: re.Match) -> str:
        try:
            payload = json.loads(m.group(3))
        except (json.JSONDecodeError, ValueError):
            return m.group(0)
        cleaned = _enforce_component(m.group(2), payload, index)
        if cleaned == payload:
            return m.group(0)
        return f"{m.group(1)}\n{json.dumps(cleaned, ensure_ascii=False)}\n{m.group(4)}"

    def fix_markup(markup: str) -> str:
        markup = _TABLE_RE.sub(lambda t: _enforce_html_table(t.group(0), index), markup)
        # Wrapped in ">...<" so text before the first tag (marker output) is checked too.
        return _enforce_prose(">" + markup + "<", index)[1:-1]

    # Component JSON is handled on its own so the prose pass never touches it.
    out, last = [], 0
    for m in _COMPONENT_RE.finditer(html):
        out.append(fix_markup(html[last:m.start()]))
        out.append(fix_component(m))
        last = m.end()
    out.append(fix_markup(html[last:]))
    return "".join(out)

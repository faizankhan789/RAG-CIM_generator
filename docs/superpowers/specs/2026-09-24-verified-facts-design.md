# Verified Facts — no misplaced figures in any CIM

Date: 2026-09-24
Status: Approved by user ("yes go ahead").

## Problem

Findings reach the generator as prose ("EBITDA $3,950,000, up from $3,180,000 in
FY2024"). The model assigns numbers to metric/period itself and sometimes gets it
wrong — real run: FY2024 EBITDA $3,180,000 was also shown under FY2023. The value-only
guard (`_drop_untraceable_figures`) can't catch it: the number exists in the source.

Rule from the user: the design template (built-in or uploaded — it can be anything)
supplies design only; every figure comes from the listing data, in its right place,
and anything the data doesn't support is dropped.

## Design

1. **Facts table** (`core/facts.py:build_facts`) — one Claude call (temperature 0)
   before generation turns listing XML + findings into
   `{"facts": [{metric, period, value, quote}]}`.
2. **Code verification** (`verify_facts`) — a fact survives only if its quote exists
   in the source text (whitespace/case-normalized) and the quote contains both the
   value (numeric match) and, when given, the period (normalized period key).
3. **Generator gets the verified facts** as the only allowed source of figures for
   tables, KPIs and charts (both prompts): same metric + same period, else "—".
4. **Post-generation enforcement** (`enforce_facts`), template-agnostic:
   - HTML `<table>`s (parsed per table with BeautifulSoup, only that table is
     re-serialized): period axis detected from the header row OR first column; a
     cell whose value isn't a fact for that metric+period becomes "—"; rows/columns
     left all "—" are removed. Tables with row/col spans are skipped.
   - Built-in component JSON: `data-table` (same grid logic), `chart-bar` periods,
     `stat-strip` cards whose label names a period.
   - Prose: a figure tied to a period (nearest period mention, no other figure in
     between, same sentence) that the facts place in a different period → sentence
     dropped.
   Metric matching: best token-overlap match against fact metrics; a weak match falls
   back to "value belongs to another period → '—'".
5. Existing guards (`_restore_exact_figures`, `_drop_untraceable_figures`) stay as the
   safety net. If the facts call fails or returns nothing, generation proceeds
   exactly as before (no enforcement).

## Cost

One extra text-only call per CIM: ~+5-10% cost, +10-30 s.

## Limits

A figure the source never ties to a period can't be placed in a period column — it
only appears where no period is needed. Prose check is heuristic (nearest mention).

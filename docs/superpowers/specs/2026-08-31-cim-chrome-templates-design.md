# CIM Chrome Templates — Code-Rendered Cover/TOC/Header/Footer — Design Spec

Date: 2026-08-31
Status: Approved by user, proceeding to implementation plan.
Scope: the **5 built-in design templates** (`classic`, `minimalist`, `editorial`, `luxury`, `startup` in `core/templates.py`) only. Explicitly excludes the PDF-upload custom-template feature (`docs/superpowers/specs/2026-08-31-pdf-template-upload-design.md`) — that path is untouched by this spec.

## Problem

Today, `generate_cim_html()` (`core/llm.py`) sends the ENTIRE CIM — cover page, top bar, bottom bar, table of contents, stat strip, all 10 section headers, all section body content, disclaimer/footer — to Claude as one freeform HTML/CSS write, steered only by prose (`layout_notes`/`cover_override`/`section_header_override` in `core/templates.py`, plus the base `PAGE 1 — COVER` etc. specs in `core/llm.py`). Because the LLM re-derives the literal markup/CSS from that prose on every single generation, the same class of bug can recur inconsistently between runs — e.g. the `luxury` template's gold corner-bracket frame colliding with the bottom bar, found and prose-patched twice in one session, with no guarantee a future generation won't drift again in some other way.

The parts of the document that should look identical on every generation — cover, top bar, bottom bar, section-header band, table of contents — should actually BE identical: driven from one fixed, verified, coded HTML/CSS skeleton per template, with only real listing data filled in. The genuinely variable body content (financials, SWOT, org chart, etc. — whose presence/length depends on what data exists for that listing) stays LLM-authored, exactly as today.

## Requirements (from user discussion)

- Cover page, top bar, bottom bar, section-header band, and table of contents are rendered from one fixed, coded HTML/CSS skeleton per template — layout and styling byte-identical across every generation for a given template.
- Only real listing data is injected into that skeleton: business name, asking price, industry label, today's date, logo, section numbers/titles. Never hardcoded/placeholder content.
- Body section content (Executive Summary prose, financials, SWOT, org chart, images, etc.) remains fully LLM-authored HTML, exactly as today — it must keep varying per listing, so it cannot be hardcoded.
- Before any of the 5 skeletons is frozen, that template's current cover/TOC/header/footer output must be generated and visually verified bug-free (correct z-index/layering, no overlaps) — fixing anything found, same as the `luxury` bottom-bar fix already made this session — since the frozen skeleton becomes the permanent source of truth from then on.
- No second LLM call and no meaningful cost/latency increase — `generate_cim_html()` stays a single call; only its **output contract** changes (marker-delimited content instead of a full HTML document).
- The PDF-upload custom-template feature is unaffected — it keeps generating a full HTML document via the existing (unchanged) code path.

## Architecture

**Today:** one LLM call → one full HTML document → `formatter_node` passes it through (only substitutes the `<!-- LOGO -->` marker).

**New:** the LLM call stops authoring chrome markup entirely. It emits its content wrapped in simple HTML-comment markers; a new assembler parses those markers and renders the real chrome around them from a fixed, coded skeleton.

```
formatter_node (nodes/formatter.py)
   │  same inputs as today: listing_name, asking_price, logo_url,
   │  template_id, custom_template
   ▼
generate_cim_html()  (core/llm.py)
   │  IF custom_template is set (PDF-upload flow): UNCHANGED — full HTML
   │    document generation, exactly as today. Stop here.
   │  ELSE (one of the 5 built-in templates): new marker-based prompt —
   │    Claude no longer writes cover/top-bar/bottom-bar/TOC/header-band
   │    markup at all. It emits, in order:
   │      <!-- INDUSTRY: Restaurant & Food Service -->
   │      <!-- STATS --> ...stat-strip card HTML (LLM-authored, as today)... <!-- /STATS -->  (omitted if no metrics)
   │      <!-- SECTION num="I" title="Investment Overview" -->
   │        ...section body HTML (LLM-authored, as today: subtopics,
   │        tables, charts, images, "only include sections with real
   │        data" rule — unchanged)...
   │      <!-- /SECTION -->
   │      ...repeated per included section...
   │  Returns this marker-delimited text (NOT full HTML) for the 5
   │  built-in templates.
   ▼
core/cim_assembler.py (new)  ── only invoked for the 5 built-in templates
   │  1. Regex-parse the marker text → {industry, stats_html, sections:
   │     [{num, title, body_html}, ...]}
   │  2. Select core/chrome/<template_id>.py for the active template
   │  3. Call its render(business_name, asking_price, industry, date,
   │     logo_html, stats_html, sections) → returns the COMPLETE final
   │     HTML document: coded cover → coded TOC (built from the parsed
   │     `sections` list) → stats_html (if present) → for each section,
   │     a coded header band wrapping that section's body_html → coded
   │     footer/disclaimer.
   │  4. Existing logo-marker substitution logic moves here (unchanged
   │     behavior, just relocated from formatter_node).
   ▼
Final CIM HTML
```

## Components

### 1. `core/chrome/` (new package — one module per built-in template)
- `core/chrome/classic.py`, `minimalist.py`, `editorial.py`, `luxury.py`, `startup.py`.
- Each exposes one function: `render(business_name: str, asking_price: str, industry: str, date_str: str, logo_html: str, stats_html: str, sections: list[dict]) -> str`.
- Contains literal, verified HTML/CSS — palette/fonts/spacing baked in per that template (sourced from the corresponding `core/templates.py` entry's `palette`/`fonts`, but as literal values now, not prompt text) — with only the function arguments as data slots (via f-strings or a small Jinja2 template per module — implementer's choice, consistent across all 5).
- Each module owns: cover markup, top bar, bottom bar, TOC (built by iterating `sections`), the section-header band wrapper repeated per section, and the footer/disclaimer band.
- **Before writing each module**, generate one real CIM with today's pipeline for that template, visually verify the cover/TOC/header/footer is bug-free (fix anything found — mirrors the `luxury` bottom-bar-overlap fix already done this session), and only then encode that verified output as the literal skeleton. Skeletons are derived from a checked-good render, not transcribed blind from the existing prose specs (which are already known to have had at least one bug).

### 2. `core/cim_assembler.py` (new)
- `assemble(marker_text: str, template_id: str, business_name: str, asking_price: str, date_str: str, logo_html: str) -> str`.
- Parses `<!-- INDUSTRY: ... -->`, optional `<!-- STATS -->...<!-- /STATS -->`, and repeated `<!-- SECTION num=".." title=".." -->...<!-- /SECTION -->` blocks via regex.
- Looks up and calls the matching `core/chrome/<template_id>.render(...)`.
- Owns the logo-marker substitution step (moved here from `core/llm.py`/`formatter_node`, behavior unchanged).

### 3. `core/llm.py` (modified)
- `generate_cim_html()` branches on whether `custom_template` is set:
  - Set (PDF-upload flow) → **unchanged**: existing full-HTML-document prompt and post-processing, exactly as today.
  - Unset, one of the 5 built-in `template_id`s → new marker-based prompt (replaces the cover/TOC/section-header/footer instructions with the marker-output contract above); content-writing rules for section selection, subtopics, tables, charts, and images are unchanged, only the outer wrapping markup is removed from what Claude is asked to produce.
- Returns the raw marker text (not full HTML) in the new branch — caller (`formatter_node`) is responsible for routing it to the assembler.

### 4. `nodes/formatter.py` (modified)
- After `generate_cim_html()` returns: if `custom_template` was set, use the returned string as-is (today's behavior). Otherwise, pass the returned marker text into `core/cim_assembler.assemble(...)` along with `listing_name`, `asking_price`, `template_id`, fetched logo, and today's date, and use its return value as `cim_output`.

## Error Handling

- **Missing/malformed `INDUSTRY` marker**: assembler falls back to a generic label (e.g. "PRIVATE BUSINESS SALE"), logs an error — same fallback pattern already used for the missing-`<!-- LOGO -->`-marker case in `core/llm.py`.
- **No `SECTION` markers found at all** (contract violation — old-style full HTML leaked through, or a malformed response): treated as a hard failure of the new contract. Assembler logs an error and falls back to returning the raw LLM text through unchanged, rather than emitting a broken half-assembled document.
- **Malformed/unclosed individual `SECTION` marker**: skip only that section, log it, keep the rest — one bad section must not fail the whole document (same principle as the existing `_is_valid_image` behavior, which drops one bad image without failing the run).
- **Missing `STATS` marker**: valid and expected when a listing has no extractable metrics — stat strip is simply omitted, exactly as today.

## Testing

- Unit tests per `core/chrome/<id>.py`: fixed sample input → exact expected HTML output, one test per template (all 5).
- Unit tests for `core/cim_assembler.py`'s parser: well-formed input; missing `INDUSTRY`; no `SECTION` markers at all; malformed/unclosed `SECTION`; duplicate section numbers; special characters in business name/title (e.g. an apostrophe, as in "O'Sarracino").
- Integration test: a fixture marker-text LLM response → `assemble()` → assert the cover contains the correct name/price/industry, TOC entries match the parsed section list 1:1 (same discipline already required of the LLM today), and each section is wrapped in the correct template's header-band markup.
- Regression test: confirm the `custom_template` (PDF-upload) path is completely unaffected — still produces a full HTML document via the unchanged legacy code path.

## Explicitly Out of Scope

- The PDF-upload custom-template feature — untouched, keeps its existing full-HTML LLM generation path.
- Freezing the stat-strip's own layout into chrome — it stays LLM-authored HTML (only its position between TOC and Section I is fixed, via the `STATS` marker). A reasonable future phase, not required here.
- Any change to body-section content-generation logic (subtopic selection, tables, charts, SWOT, org chart, image placement) — unchanged, fully LLM-authored per listing, exactly as today.

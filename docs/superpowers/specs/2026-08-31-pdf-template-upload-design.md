# PDF Custom Template Upload — Design Spec

Date: 2026-08-31
Status: Approved by user, proceeding to implementation plan.
Scope: **PDF only.** DOC/DOCX and HTML uploads are explicitly deferred to later phases — not part of this spec.

## Problem

The CRM's "Vertica AI" CIM flow already offers two ways to generate a CIM:
1. Quick Create — default design, generated immediately.
2. Pre-defined Custom Template — user picks one of 5 hardcoded design templates (`core/templates.py`); Claude writes real listing content into that fixed layout (`core/llm.py`).

A third option, "Upload Your Custom Template," was added to the CRM UI as a placeholder ("Coming Soon" modal only — no backend). This spec defines the real implementation for the PDF case: a user uploads a PDF that already has the visual design they want (colors, fonts, section layout, icons), and the system generates a new CIM, with real listing data, that looks like that PDF.

## Requirements (from user discussion)

- Layout, colors, fonts, section structure, icons/bullets must be extracted from the uploaded PDF **without an LLM** — deterministic parsing only.
- Per-section coloring/structure must be captured, not just one global palette — sections in the source PDF can differ from each other.
- The actual CIM **content** (exec summary prose, financial narrative, etc.) is still written by Claude from real listing data, poured into the extracted style — this matches exactly how the existing 5 built-in templates already work. No change to that part of the pipeline.
- Body text cannot stay pixel-frozen: a new deal's content will be a different length than whatever text was in the original PDF, so text reflows within the extracted style (same as a webpage). Fonts/colors/spacing/icons/section order stay exact; only body-text line-wrapping/height is naturally fluid.

## Architecture

**Key finding from codebase inspection (corrects the original plan):** `core/templates.py`'s `TEMPLATES[id]` entries are plain dicts — `{name, palette{primary,accent,light,mid}, fonts{heading,body}, layout_notes, cover_override, section_header_override, headings{...}}`. `core/llm.py:_build_template_directive(template: dict)` turns that dict into the prompt block that steers Claude's HTML output; `generate_cim_html()` just does `template = get_template(template_id)` and passes it in. None of the 5 built-in templates are literal HTML skeletons — they're all style-directive dicts that Claude generates fresh HTML from every time, same as this feature needs to do.

That means an uploaded PDF only needs to be turned into **a dict of that same shape** — not a new rendering path. Uploaded PDF becomes a **6th, dynamically-derived template dict**, alongside the 5 hardcoded ones, consumed by the exact same `_build_template_directive()` / `generate_cim_html()` code, unchanged except for one new optional parameter.

```
Upload PDF
   │
   ▼
PDFStyleExtractor (PyMuPDF)   ── deterministic, no LLM
   │  → measures: dominant colors (text + fill colors),
   │    heading/body font families (from embedded font names + flags),
   │    cover alignment, banded/pill/rule decoration around headings,
   │    bullet glyph style, heading text per detected section
   │  → composes a TEMPLATES-shaped dict via fixed rule-to-sentence
   │    lookups (deterministic string composition, not free text generation)
   ▼
SectionMatcher                ── deterministic, no LLM
   │  → maps each detected heading to the canonical CIM taxonomy
   │    (Executive Summary, Company Overview, Financial Info, ...)
   │  → becomes the dict's "headings" field (standard heading → PDF's own label)
   │  → unmatched canonical sections fall back to the classic template's
   │    wording for that heading, flagged (not dropped silently)
   ▼
template dict — same shape as core/templates.py TEMPLATES["classic"] etc.
   │
   ▼
core/llm.py generate_cim_html(..., custom_template=<dict>)  ── 1-line change:
   │  template = custom_template or get_template(template_id)
   │  everything else (prompt building, Claude call, content writing) unchanged
   ▼
Final CIM HTML
```

## Components

### 1. `core/pdf_style_extractor.py` (new)
- Uses `PyMuPDF` (`fitz`) — new dependency, add to `requirements.txt`.
- Input: uploaded PDF bytes.
- Output: a plain dict, same shape as one entry of `core/templates.py`'s `TEMPLATES` — `{"id", "name", "tagline", "allow_brand_override", "palette": {...}, "fonts": {...}, "layout_notes", "cover_override", "section_header_override", "headings": {...}}`.
- Reads text runs via `page.get_text("dict")` (font/size/color/position per span) to find heading-sized/weighted text and measure dominant colors.
- Reads shapes/fills via `page.get_drawings()` for background bands/pills/rules behind headings and the cover.
- `palette`/`fonts` are measured values (hex colors, mapped font-stacks). `layout_notes`/`cover_override`/`section_header_override` are composed from a fixed set of deterministic rule → sentence templates (e.g. "band detected behind page-1 title" → a canned sentence describing that, with the measured color substituted in) — not free-text generation.
- No text = no OCR in v1 — if a page has zero extractable text spans, fail fast with a clear error (see Error Handling).

### 2. `core/section_matcher.py` (new)
- Canonical CIM section list, reused from what `core/llm.py` already treats as the fixed taxonomy (Executive Summary, Company Overview, Financial Information, Operations, Marketing and Sales, Legal and Regulatory, Human Resources, Growth Opportunities, Risks, Appendix).
- Small synonym table per section (e.g. "Financials"/"Financial Overview" → "Financial Information").
- Matches each extracted heading block (identified by the extractor as heading-sized/weighted text) against the taxonomy by normalized text comparison.
- Output feeds directly into the template dict's `"headings"` field (same shape as `TEMPLATES["classic"]["headings"]`: canonical heading → PDF's own label text).
- Sections in the canonical list with no match in the PDF keep the `classic` template's wording for that heading (borrowed default) so the generated CIM is still complete — this is flagged in the response, never silent.

### 3. `core/llm.py` (one-line extension, not rewritten)
- `generate_cim_html()` gains one new optional parameter: `custom_template: dict | None = None`.
- Its only change: `template = custom_template or get_template(template_id)` in place of the current unconditional `get_template(template_id)` call.
- `_build_template_directive()`, the Claude call, and all content-writing instructions are completely unchanged — they already work off a plain dict, which is exactly what the extractor now also produces.

### 4. `server.py` (extended)
- New endpoint: `POST /template/upload` — accepts a PDF file, runs `PDFStyleExtractor` + `SectionMatcher`, returns the resulting template dict (or a validation error).
- `CIMRequest` (`models.py`) gains one new optional field: `custom_template: dict | None = None`. Wherever an existing endpoint currently forwards `req.template_id` into `generate_cim_html`, it also forwards `req.custom_template` — the model already carries both; the field is simply unset for the two existing flows (Quick Create, Pre-defined Custom Template).
- No persistent template library in v1 (YAGNI) — the returned dict flows through the same request/session as today's flow; it is not saved server-side for reuse across separate generations. Persisting/reusing uploaded templates is a candidate future phase, not part of this spec.

### 5. `view.php` (CRM UI, extended)
- Replace the current "Coming Soon" modal behavior on the "Upload Your Custom Template" card with a real file-input (PDF only, `accept="application/pdf"`).
- On upload, call `POST /template/upload`; on success, proceed into the same "pick listing/files → generate" flow already used by the Pre-defined Custom Template path, carrying the returned template dict as `custom_template` in the generate request instead of a `template_id`.
- On extractor/validation error, show the error message in place of the coming-soon copy.

## Error Handling

- **No extractable text** (scanned/image-only PDF): reject with "This PDF has no selectable text — we can't read its layout. Try a text-based PDF." No OCR in v1.
- **Fewer than N canonical sections matched**: proceed, but fall back to default style for unmatched sections and surface a warning to the user (not a silent gap) — mirrors the existing "TOC must match rendered sections" discipline already enforced in `core/llm.py` for the 5 built-in templates.
- **Corrupt/invalid file**: standard upload validation, reject with a clear message before extraction is attempted.

## Testing

- Unit tests for `PDFStyleExtractor` against a small set of sample PDF fixtures — verify font/color/position extraction correctness.
- Unit tests for `SectionMatcher` — canonical matches, synonym matches, and the unmatched/default-fallback path.
- Integration test: upload a sample PDF → run generation → structurally verify (regex, not just visual) that the output HTML actually contains the extracted colors/fonts, consistent with the existing structural-verification approach already used for the 5 built-in templates (ToC-to-section match, no marker conflicts).

## Explicitly Out of Scope (this spec)

- DOC/DOCX upload support (separate future phase — noted as easier than PDF since Word's XML already carries semantic paragraph styles).
- HTML upload support (separate future phase — easiest case, DOM already semantic).
- OCR for scanned/image-only PDFs.
- Persistent, reusable template library (saving an uploaded template for future generations beyond the current session).
- Manual tagging UI — not needed; taxonomy-matching handles section identification deterministically.

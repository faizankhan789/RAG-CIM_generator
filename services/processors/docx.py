"""
DOCX structure detection and boundary-aware segmentation.

Streams the underlying OOXML via ``xml.etree.ElementTree.iterparse``
so memory usage stays constant regardless of file size (safe for 1 GB+
files).  No external dependencies beyond the standard library.

Supported formats: .docx, .docm, .dotx  (all OOXML-based).
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Generator
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OOXML namespace helpers
# ---------------------------------------------------------------------------

_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _qn(tag: str) -> str:
    """Clark-notation qualified name in the wordprocessingml namespace."""
    return f"{{{_NS}}}{tag}"


def _attr(el: ET.Element, local: str) -> str | None:
    """Attribute lookup: namespace-qualified first, then bare local name."""
    return el.get(_qn(local)) or el.get(local)


# Frequently-used qualified element names.
_W_P           = _qn("p")
_W_TBL         = _qn("tbl")
_W_TR          = _qn("tr")
_W_TC          = _qn("tc")
_W_R           = _qn("r")
_W_T           = _qn("t")
_W_TAB         = _qn("tab")
_W_BR          = _qn("br")
_W_CR          = _qn("cr")
_W_PPR         = _qn("pPr")
_W_RPR         = _qn("rPr")
_W_PSTYLE      = _qn("pStyle")
_W_B           = _qn("b")
_W_B_CS        = _qn("bCs")
_W_SZ          = _qn("sz")
_W_SPACING     = _qn("spacing")
_W_NUM_PR      = _qn("numPr")
_W_STYLE       = _qn("style")
_W_NAME        = _qn("name")
_W_BASED_ON    = _qn("basedOn")
_W_OUTLINE_LVL = _qn("outlineLvl")
_W_SECT_PR     = _qn("sectPr")
_W_HIGHLIGHT   = _qn("highlight")
_W_SHD         = _qn("shd")

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

_STRUCTURE_SAMPLE_SIZE = 500   # max elements sampled by is_structured_docx
_STRUCTURE_DENSITY_MIN = 0.08  # >= 8 % of sampled elements must carry signal
_LARGE_SPACING_TWIPS   = 480   # ~24 pt  /  1/3 inch
_FONT_JUMP_HALF_PT     = 4     # > 2 pt increase triggers a boundary
_MAX_KEY_LEN           = 80    # chars permitted on the left side of a KV line
_HEADING_NAME_RE = re.compile(r"^heading\s*(\d+)$", re.IGNORECASE)
_LEADING_ENUM_RE = re.compile(r"^[\dA-Za-z]{1,3}[.)]\s+")


def _is_key_value(text: str) -> bool:
    """
    Structural KV check — matches ``label: value`` without a full regex.

    Accepts:
      - alphanumeric keys up to _MAX_KEY_LEN chars
      - numbered prefixes such as ``1. Name: John`` (stripped before checks)
      - up to 4 words in the key (tight bound to reject sentence fragments)

    Rejects:
      - keys with periods (sentence fragments)
      - empty keys or values
    """
    idx = text.find(":")
    if idx <= 0 or idx > _MAX_KEY_LEN:
        return False
    key = text[:idx].strip()
    val = text[idx + 1:].strip()
    if not key or not val:
        return False

    # Strip leading list enumerators like "1. ", "a) ", "iv)" so numbered
    # KV pairs inside lists still qualify.
    key = _LEADING_ENUM_RE.sub("", key).strip()
    if not key or not key[0].isalnum():
        return False

    # Sentence fragments usually contain a period or run long — reject both.
    if "." in key:
        return False
    if len(key.split()) > 4:
        return False
    return True


def _is_meaningful_shading(shd_el: ET.Element | None) -> bool:
    """
    True when a ``w:shd`` element represents a real background fill.

    OOXML uses ``val="clear"`` + ``fill="auto"`` to mean "no shading", so we
    must ignore those and treat only actual colors (non-auto, non-white) as
    emphasis.
    """
    if shd_el is None:
        return False
    val = _attr(shd_el, "val")
    if val and val.lower() in ("nil", "none"):
        return False
    fill = _attr(shd_el, "fill")
    if fill is None:
        # No fill attribute — fall back to val (e.g. pattern fills).
        return val is not None and val.lower() != "clear"
    fill_lower = fill.lower()
    if fill_lower in ("auto", "ffffff", "fff"):
        return False
    return True


def _looks_like_title(text: str) -> bool:
    """
    True for ALL-CAPS *or* Title Case short lines — Rule 5 (extended).

    A line counts as a title when it is short, has no trailing punctuation,
    and either:
      - every alphabetic char is uppercase, or
      - every significant word starts with an uppercase letter.
    """
    if len(text) < 2 or len(text) > 120:
        return False
    if text.endswith((".", "?", "!", ",", ";")):
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    if all(c.isupper() for c in letters):
        return True
    words = [w for w in text.split() if any(c.isalpha() for c in w)]
    if len(words) < 2 or len(words) > 12:
        return False
    small = {"a", "an", "the", "of", "and", "or", "for", "to", "in", "on", "at", "by"}
    significant = [w for w in words if w.lower() not in small]
    return all(w[:1].isupper() for w in significant)

# ---------------------------------------------------------------------------
# Internal parsed-element dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Run:
    """A contiguous run of identically-formatted text."""
    text: str
    bold: bool = False
    font_size_half_pt: int | None = None
    highlighted: bool = False   # w:highlight or meaningful w:shd on the run


@dataclass(slots=True)
class _Paragraph:
    """A body-level paragraph with extracted formatting metadata."""
    runs: list[_Run]
    heading_level: int | None = None
    is_list_item: bool = False
    spacing_before: int = 0
    section_break: bool = False   # paragraph terminates a section (w:sectPr)
    style_bold: bool = False      # boldness inherited from paragraph style
    paragraph_shaded: bool = False  # w:shd on the paragraph itself (callout)

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)

    @property
    def starts_bold(self) -> bool:
        """
        True when the first non-whitespace run is bold, considering both
        direct run formatting AND inherited paragraph-style boldness.
        """
        for r in self.runs:
            if r.text.strip():
                return r.bold or self.style_bold
        return False

    @property
    def has_highlight(self) -> bool:
        """
        True when the paragraph carries visual emphasis:
          - any run has w:highlight (Word highlighter pen), OR
          - any run has meaningful background shading, OR
          - the paragraph itself has shading (callout boxes).
        """
        if self.paragraph_shaded:
            return True
        return any(r.highlighted for r in self.runs)

    @property
    def dominant_font_size(self) -> int | None:
        """
        Most common font size (half-points) weighted by run character count.

        Prevents a single bold/large word from dominating the paragraph's
        effective size — only the majority-of-text size counts as a boundary
        signal.
        """
        weights: dict[int, int] = {}
        for r in self.runs:
            if r.font_size_half_pt and r.text:
                weights[r.font_size_half_pt] = (
                    weights.get(r.font_size_half_pt, 0) + len(r.text)
                )
        if not weights:
            return None
        return max(weights, key=weights.__getitem__)


@dataclass(slots=True)
class _Table:
    """A body-level table reduced to cell text."""
    rows: list[list[str]]

    @property
    def text(self) -> str:
        return "\n".join("\t".join(cells) for cells in self.rows)


# ---------------------------------------------------------------------------
# Public segment model  (the "return type for text splitting")
# ---------------------------------------------------------------------------


class SegmentType(str, Enum):
    """Classification of a document segment."""
    HEADING   = "heading"
    TABLE     = "table"
    KEY_VALUE = "key_value"
    LIST      = "list"
    HIGHLIGHT = "highlight"   # visually emphasized block (w:highlight or w:shd)
    PARAGRAPH = "paragraph"


@dataclass(slots=True)
class DocSegment:
    """
    A boundary-delimited chunk of a document.

    Downstream consumers (embeddings, retrieval, …) receive a stream of
    these objects rather than raw text, so they can adapt strategy per type.
    """
    type: SegmentType
    text: str
    heading_level: int | None = None


# ---------------------------------------------------------------------------
# Style resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _StyleInfo:
    name: str | None            # display name (e.g. "Heading 1")
    outline_level: int | None   # 0-8 for headings, None otherwise (9 = body)
    based_on: str | None
    bold: bool | None           # inherited run boldness from the style
    font_size_half_pt: int | None
    has_num_pr: bool            # style defines numPr (bullets / numbering)


def _load_styles(zf: zipfile.ZipFile) -> dict[str, _StyleInfo]:
    """
    Parse ``word/styles.xml`` into {style_id: _StyleInfo}.

    Captures enough metadata to support:
      - heading detection via outlineLvl OR style name
      - bold / font-size inheritance from paragraph styles
    """
    try:
        with zf.open("word/styles.xml") as f:
            tree = ET.parse(f)
    except KeyError:
        return {}

    styles: dict[str, _StyleInfo] = {}
    for el in tree.iter(_W_STYLE):
        sid = _attr(el, "styleId")
        if not sid:
            continue

        name_el = el.find(_W_NAME)
        name = _attr(name_el, "val") if name_el is not None else None

        bo_el = el.find(_W_BASED_ON)
        based_on = _attr(bo_el, "val") if bo_el is not None else None

        outline: int | None = None
        has_num_pr = False
        ppr = el.find(_W_PPR)
        if ppr is not None:
            olvl = ppr.find(_W_OUTLINE_LVL)
            if olvl is not None:
                try:
                    raw = int(_attr(olvl, "val") or "")
                    # OOXML treats 9 as "body text" — explicitly NOT a heading.
                    if 0 <= raw <= 8:
                        outline = raw
                except ValueError:
                    pass
            if ppr.find(_W_NUM_PR) is not None:
                has_num_pr = True

        bold: bool | None = None
        font_size: int | None = None
        rpr = el.find(_W_RPR)
        if rpr is not None:
            for tag in (_W_B, _W_B_CS):
                b_el = rpr.find(tag)
                if b_el is not None:
                    v = _attr(b_el, "val")
                    bold = v is None or v not in ("0", "false")
                    break
            sz = rpr.find(_W_SZ)
            if sz is not None:
                try:
                    font_size = int(_attr(sz, "val") or "")
                except ValueError:
                    pass

        styles[sid] = _StyleInfo(
            name=name,
            outline_level=outline,
            based_on=based_on,
            bold=bold,
            font_size_half_pt=font_size,
            has_num_pr=has_num_pr,
        )
    return styles


def _heading_level(
    style_id: str | None, styles: dict[str, _StyleInfo],
) -> int | None:
    """
    Resolve a paragraph style to a heading level (1-9) or None.

    Strategy (stops at first hit):
      1. Walk the ``basedOn`` chain looking for an outlineLvl.
      2. Match the style id or display name against ``Heading N``.
    """
    visited: set[str] = set()
    cur = style_id
    while cur and cur not in visited:
        visited.add(cur)
        info = styles.get(cur)
        if info is None:
            break
        if info.outline_level is not None:
            return info.outline_level + 1      # OOXML outline levels are 0-based

        # Name-based fallback — catches styles that carry no outlineLvl but
        # are clearly headings (e.g. custom "Heading1" / "Heading 2").
        for candidate in (cur, info.name):
            if not candidate:
                continue
            m = _HEADING_NAME_RE.match(candidate.strip().replace("_", " "))
            if m:
                lvl = int(m.group(1))
                if 1 <= lvl <= 9:
                    return lvl

        cur = info.based_on
    return None


def _style_inherits_bold(
    style_id: str | None, styles: dict[str, _StyleInfo],
) -> bool:
    """Walk the basedOn chain and return True if any ancestor sets bold."""
    visited: set[str] = set()
    cur = style_id
    while cur and cur not in visited:
        visited.add(cur)
        info = styles.get(cur)
        if info is None:
            break
        if info.bold is not None:
            return info.bold
        cur = info.based_on
    return False


def _style_is_list(
    style_id: str | None, styles: dict[str, _StyleInfo],
) -> bool:
    """
    True if the paragraph style (or any ancestor in the basedOn chain)
    defines ``numPr``.  Catches bullets authored via styles like
    ``ListBullet``, ``ListParagraph``, ``ListNumber2`` — the common case
    in Word templates where the paragraph XML only carries ``<w:pStyle/>``.
    """
    visited: set[str] = set()
    cur = style_id
    while cur and cur not in visited:
        visited.add(cur)
        info = styles.get(cur)
        if info is None:
            break
        if info.has_num_pr:
            return True
        cur = info.based_on
    return False


# ---------------------------------------------------------------------------
# XML -> dataclass parsers
# ---------------------------------------------------------------------------


def _parse_run(r_el: ET.Element) -> _Run:
    """Extract text and key formatting from a single ``w:r`` element."""
    parts: list[str] = []
    for child in r_el:
        if child.tag == _W_T:
            parts.append(child.text or "")
        elif child.tag == _W_TAB:
            parts.append("\t")
        elif child.tag in (_W_BR, _W_CR):
            parts.append("\n")
    text = "".join(parts)

    bold = False
    font_size: int | None = None
    highlighted = False
    rpr = r_el.find(_W_RPR)
    if rpr is not None:
        for tag in (_W_B, _W_B_CS):
            b_el = rpr.find(tag)
            if b_el is not None:
                v = _attr(b_el, "val")
                bold = v is None or v not in ("0", "false")
                break
        sz = rpr.find(_W_SZ)
        if sz is not None:
            try:
                font_size = int(_attr(sz, "val") or "")
            except ValueError:
                pass

        # w:highlight — Word highlighter pen. val="none" means no highlight.
        hl = rpr.find(_W_HIGHLIGHT)
        if hl is not None:
            v = _attr(hl, "val")
            if v and v.lower() != "none":
                highlighted = True

        # Run-level shading (background fill behind the text).
        if not highlighted and _is_meaningful_shading(rpr.find(_W_SHD)):
            highlighted = True

    return _Run(
        text=text,
        bold=bold,
        font_size_half_pt=font_size,
        highlighted=highlighted,
    )


def _parse_paragraph(
    p_el: ET.Element, styles: dict[str, _StyleInfo],
) -> _Paragraph:
    """Convert a ``w:p`` element into a ``_Paragraph``."""
    runs: list[_Run] = []
    for r in p_el.iter(_W_R):
        run = _parse_run(r)
        if run.text:
            runs.append(run)

    heading_lvl: int | None = None
    is_list = False
    spacing_before = 0
    section_break = False
    style_bold = False
    paragraph_shaded = False

    ppr = p_el.find(_W_PPR)
    if ppr is not None:
        # Heading via paragraph style.
        ps = ppr.find(_W_PSTYLE)
        if ps is not None:
            style_id = _attr(ps, "val")
            heading_lvl = _heading_level(style_id, styles)
            style_bold = _style_inherits_bold(style_id, styles)
            # Style-based list detection (e.g. "ListBullet2" inheriting numPr).
            if _style_is_list(style_id, styles):
                is_list = True

        # Direct outlineLvl on the paragraph overrides style. Value 9 means
        # "body text" in OOXML — explicitly NOT a heading.
        olvl = ppr.find(_W_OUTLINE_LVL)
        if olvl is not None:
            try:
                raw = int(_attr(olvl, "val") or "")
                if 0 <= raw <= 8:
                    heading_lvl = raw + 1
                elif raw == 9:
                    heading_lvl = None
            except ValueError:
                pass

        # List detection (numPr present = numbered/bulleted item).
        if ppr.find(_W_NUM_PR) is not None:
            is_list = True

        # Spacing before (twips).
        sp = ppr.find(_W_SPACING)
        if sp is not None:
            try:
                spacing_before = int(_attr(sp, "before") or "0")
            except ValueError:
                pass

        # Section break — strongest template-level boundary (Rule 10).
        if ppr.find(_W_SECT_PR) is not None:
            section_break = True

        # Paragraph-level shading — callout / highlighted block.
        if _is_meaningful_shading(ppr.find(_W_SHD)):
            paragraph_shaded = True

    return _Paragraph(
        runs=runs,
        heading_level=heading_lvl,
        is_list_item=is_list,
        spacing_before=spacing_before,
        section_break=section_break,
        style_bold=style_bold,
        paragraph_shaded=paragraph_shaded,
    )


def _parse_table(tbl_el: ET.Element) -> _Table:
    """Convert a ``w:tbl`` element into a ``_Table``."""
    rows: list[list[str]] = []
    for tr in tbl_el.findall(_W_TR):
        cells: list[str] = []
        for tc in tr.findall(_W_TC):
            parts: list[str] = []
            for t in tc.iter(_W_T):
                if t.text:
                    parts.append(t.text)
            cells.append(" ".join(parts))
        if cells:
            rows.append(cells)
    return _Table(rows=rows)


# ---------------------------------------------------------------------------
# Streaming element generator
# ---------------------------------------------------------------------------


def _stream_elements(
    file_path: str,
) -> Generator[_Paragraph | _Table, None, None]:
    """
    Yield body-level paragraphs and tables one at a time from a DOCX file.

    Memory-efficient: uses ``iterparse`` so only one element tree branch is
    resident at a time.  Paragraphs nested inside tables are NOT yielded
    separately — their text is captured within the ``_Table`` object.
    """
    with zipfile.ZipFile(file_path) as zf:
        styles = _load_styles(zf)

        with zf.open("word/document.xml") as xml_f:
            tbl_depth = 0

            for event, elem in ET.iterparse(xml_f, events=("start", "end")):
                # Track table nesting so we only yield top-level elements.
                if elem.tag == _W_TBL:
                    if event == "start":
                        tbl_depth += 1
                    else:
                        tbl_depth -= 1
                        if tbl_depth == 0:
                            yield _parse_table(elem)
                            elem.clear()

                elif elem.tag == _W_P and event == "end" and tbl_depth == 0:
                    yield _parse_paragraph(elem, styles)
                    elem.clear()


# ---------------------------------------------------------------------------
# Structure detection  (Rule hierarchy evaluation)
# ---------------------------------------------------------------------------


def is_structured_docx(file_path: str) -> bool:
    """
    Sample the first *N* elements of a DOCX and decide whether it carries
    enough structural markup for boundary-aware splitting.

    Signal hierarchy (strongest -> weakest):
        section breaks > heading styles > tables > key-value patterns >
        font-size variation > bold starts / titles > lists

    Uses a density-based threshold (signal weight per sampled element) so
    the decision generalizes across short invoices and long contracts alike.

    Returns ``False`` on any I/O or parse error (safe fallback to
    unstructured chunking).
    """
    headings = 0
    tables = 0
    kv_lines = 0
    bold_starts = 0
    title_lines = 0
    lists = 0
    section_breaks = 0
    highlights = 0
    sampled = 0

    try:
        for i, elem in enumerate(_stream_elements(file_path)):
            if i >= _STRUCTURE_SAMPLE_SIZE:
                break
            sampled += 1

            if isinstance(elem, _Table):
                tables += 1
                continue

            text = elem.text.strip()
            if not text:
                continue

            if elem.section_break:
                section_breaks += 1
            if elem.heading_level is not None:
                headings += 1
            if elem.is_list_item:
                lists += 1
            if elem.starts_bold:
                bold_starts += 1
            if elem.has_highlight:
                highlights += 1
            if _looks_like_title(text):
                title_lines += 1
            if _is_key_value(text):
                kv_lines += 1

    except Exception:
        logger.warning(
            "Failed to inspect '%s' for structure — defaulting to unstructured",
            file_path, exc_info=True,
        )
        return False

    if sampled == 0:
        return False

    # ── Short-circuits on strong signals ───────────────────────────────
    if section_breaks >= 1 and headings >= 1:
        return True
    if headings >= 2:
        return True
    if tables >= 2 or kv_lines >= 4:
        return True

    # ── Density-based weighted score ───────────────────────────────────
    score = (
        section_breaks * 6
        + headings * 5
        + tables * 4
        + kv_lines * 3
        + highlights * 3
        + bold_starts * 2
        + title_lines * 2
        + lists
    )
    return (score / sampled) >= _STRUCTURE_DENSITY_MIN


# ---------------------------------------------------------------------------
# Boundary-aware segmentation
# ---------------------------------------------------------------------------


def split_docx(
    file_path: str, strict: bool = False,
) -> Generator[DocSegment, None, None]:
    """
    Walk the DOCX and yield ``DocSegment`` objects at every detected
    boundary.  Rules are applied in priority order:

        0. Section breaks (w:sectPr)  — strongest template boundary
        1. Heading styles             — emitted as their own segment
        2. Tables                     — always an independent block
        3. Key-value runs             — grouped consecutively
        4. Lists (bullet/numbered)    — grouped consecutively
        4.5 Highlight / shading       — grouped into a HIGHLIGHT segment
        5. Title lines (CAPS / Title) — emitted as their own HEADING segment
        6. Font-size jump             — medium, starts a new segment
        7. Bold at line start         — weaker sub-section boundary
        8. Large paragraph spacing    — weakest

    Each non-heading segment carries the ``heading_level`` of the nearest
    preceding heading so downstream retrieval can reconstruct hierarchy.

    :param strict: raise on parse errors instead of logging and yielding
        the partial result so far.
    """

    acc: list[str] = []
    acc_type = SegmentType.PARAGRAPH
    current_heading_level: int | None = None   # nearest preceding heading
    prev_font: int | None = None

    # -- helpers ----------------------------------------------------------

    def _flush() -> DocSegment | None:
        nonlocal acc, acc_type
        if not acc:
            return None
        seg = DocSegment(
            type=acc_type,
            text="\n".join(acc),
            heading_level=current_heading_level,
        )
        acc = []
        acc_type = SegmentType.PARAGRAPH
        return seg

    # -- main loop --------------------------------------------------------

    try:
        for elem in _stream_elements(file_path):

            # ── Rule 2: Tables — always an independent block ─────────
            if isinstance(elem, _Table):
                seg = _flush()
                if seg:
                    yield seg
                table_text = elem.text.strip()
                if table_text:
                    yield DocSegment(
                        type=SegmentType.TABLE,
                        text=table_text,
                        heading_level=current_heading_level,
                    )
                continue

            text = elem.text.strip()

            # ── Rule 0: Section break — flush; handled after body ────
            if elem.section_break:
                if text:
                    acc.append(text)
                seg = _flush()
                if seg:
                    yield seg
                prev_font = None
                continue

            if not text:
                continue

            # ── Rule 1: Heading styles — emit as own segment ────────
            if elem.heading_level is not None:
                seg = _flush()
                if seg:
                    yield seg
                current_heading_level = elem.heading_level
                yield DocSegment(
                    type=SegmentType.HEADING,
                    text=text,
                    heading_level=elem.heading_level,
                )
                prev_font = elem.dominant_font_size
                continue

            # ── Rule 3: Key-value patterns — group consecutive ───────
            # Checked before Title / List so "Section 1: Overview" style
            # lines resolve to KV, matching the spec precedence.
            if _is_key_value(text):
                if acc_type != SegmentType.KEY_VALUE:
                    seg = _flush()
                    if seg:
                        yield seg
                    acc_type = SegmentType.KEY_VALUE
                acc.append(text)
                prev_font = elem.dominant_font_size
                continue

            # ── Rule 4: Lists — group consecutive items ──────────────
            if elem.is_list_item:
                if acc_type != SegmentType.LIST:
                    seg = _flush()
                    if seg:
                        yield seg
                    acc_type = SegmentType.LIST
                acc.append(text)
                prev_font = elem.dominant_font_size
                continue

            # ── Rule 4.5: Highlighted / shaded content ───────────────
            # Callout boxes and w:highlight runs group into a dedicated
            # HIGHLIGHT segment so downstream retrieval can weight them.
            if elem.has_highlight:
                if acc_type != SegmentType.HIGHLIGHT:
                    seg = _flush()
                    if seg:
                        yield seg
                    acc_type = SegmentType.HIGHLIGHT
                acc.append(text)
                prev_font = elem.dominant_font_size
                continue

            # ── Rule 5: Title lines (ALL-CAPS or Title Case) ────────
            if _looks_like_title(text):
                seg = _flush()
                if seg:
                    yield seg
                yield DocSegment(
                    type=SegmentType.HEADING,
                    text=text,
                    heading_level=current_heading_level,
                )
                prev_font = elem.dominant_font_size
                continue

            # ── Leaving a grouped type (LIST / KEY_VALUE / HIGHLIGHT) ─
            if acc_type in (
                SegmentType.LIST,
                SegmentType.KEY_VALUE,
                SegmentType.HIGHLIGHT,
            ):
                seg = _flush()
                if seg:
                    yield seg

            cur_font = elem.dominant_font_size

            # ── Rule 6: Font-size jump (> 2 pt increase) ────────────
            if (
                cur_font is not None
                and prev_font is not None
                and cur_font > prev_font + _FONT_JUMP_HALF_PT
            ):
                seg = _flush()
                if seg:
                    yield seg
                acc.append(text)
                prev_font = cur_font
                continue

            # ── Rule 7: Bold at line start ───────────────────────────
            if elem.starts_bold and acc:
                seg = _flush()
                if seg:
                    yield seg
                acc.append(text)
                prev_font = cur_font
                continue

            # ── Rule 8: Large paragraph spacing ──────────────────────
            if elem.spacing_before >= _LARGE_SPACING_TWIPS and acc:
                seg = _flush()
                if seg:
                    yield seg

            # ── Default: accumulate ──────────────────────────────────
            acc.append(text)
            prev_font = cur_font

    except Exception:
        if strict:
            raise
        logger.error(
            "Error during DOCX segmentation of '%s'", file_path, exc_info=True,
        )

    # Flush anything remaining.
    seg = _flush()
    if seg:
        yield seg

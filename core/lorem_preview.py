"""Lorem-ipsum preview of a saved template.

The template picker's preview (server.py /template/saved/{id}/preview) shows the
uploaded template's own design, but with every piece of its text replaced by lorem
ipsum of about the same length, case, size and colour — so the original company's
names, products and figures never show there. Only the preview changes; CIM
generation still uses the real template file.

- PDF (which includes Word/PowerPoint templates, rendered to PDF at upload): each
  text span is removed with a redaction that keeps images and vector graphics, then
  lorem text is written back at the same position, size, colour and weight. PDF
  metadata and bookmarks (often the company name) are cleared.
- HTML/XML: text nodes, <title> and alt/title attributes; markup and CSS untouched.

Limit: text that is part of an image (a logo, a scanned page) can't be changed.
"""

from __future__ import annotations

import re
import zlib

import pymupdf

_LOREM = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut "
    "labore et dolore magna aliqua enim ad minim veniam quis nostrud exercitation ullamco laboris "
    "nisi aliquip ex ea commodo consequat duis aute irure in reprehenderit voluptate velit esse "
    "cillum fugiat nulla pariatur excepteur sint occaecat cupidatat non proident sunt culpa qui "
    "officia deserunt mollit anim id est laborum"
).split()

_WORD_RE = re.compile(r"[A-Za-z]+|\d")


def _lorem_word(length: int, start: int, avoid: str) -> str:
    """A lorem word of exactly `length` letters if one exists, else the closest shorter
    one — so a lorem line is about as wide as the original and needs almost no
    stretching. Never `avoid` itself (the original word)."""
    n = len(_LOREM)
    for size in range(min(length, 13), 0, -1):          # longest lorem word has 13 letters
        for i in range(n):
            word = _LOREM[(start + i) % n]
            if len(word) == size and word != avoid:
                return word
    return "e" * length


def lorem_text(text: str) -> str:
    """Same shape as text — whitespace, punctuation, case pattern, digits as 0 — with
    every word swapped for lorem ipsum. Deterministic, never longer than the input."""
    start = zlib.crc32(text.encode("utf-8"))
    counter = 0

    def swap(m: re.Match) -> str:
        nonlocal counter
        word = m.group(0)
        if word.isdigit():
            return "0"
        counter += 1
        new = _lorem_word(len(word), start + counter * 7, word.lower())
        if len(word) > 1 and word.isupper():
            return new.upper()
        return new.capitalize() if word[0].isupper() else new

    return _WORD_RE.sub(swap, text)


# ── PDF ───────────────────────────────────────────────────────────────────────

# The PDF "serif" flag is often wrong (a real template's sans-serif Montserrat title
# came out in Times), so the family is chosen from the font NAME; flags only add bold/italic.
_SERIF_FONT_RE = re.compile(
    r"(?<!sans)(?<!sans-)serif|times|georgia|garamond|minion|cambria|caladea|palatino|baskerville|bodoni|"
    r"didot|playfair|merriweather|lora\b|crimson|cormorant|antiqua|schoolbook|charter|tinos|trajan",
    re.IGNORECASE,
)
_MONO_FONT_RE = re.compile(r"courier|mono|consolas|menlo", re.IGNORECASE)


def _base14(font_name: str, flags: int) -> str:
    name = font_name or ""
    bold = bool(flags & 16) or bool(re.search(r"bold|black|heavy|semibold|demi", name, re.IGNORECASE))
    italic = bool(flags & 2) or bool(re.search(r"italic|oblique", name, re.IGNORECASE))
    style = {(False, False): "", (True, False): "bo", (False, True): "it", (True, True): "bi"}[(bold, italic)]
    if _MONO_FONT_RE.search(name):
        return "co" + (style or "ur")
    if _SERIF_FONT_RE.search(name):
        return "ti" + (style or "ro")
    return "he" + (style or "lv")


def _rgb(color: int) -> tuple[float, float, float]:
    return ((color >> 16) & 255) / 255, ((color >> 8) & 255) / 255, (color & 255) / 255


# Horizontal stretch allowed when fitting a lorem line to the original width — beyond
# this range letters visibly distort (look bold/condensed), so width is only approximate.
_MIN_SCALE, _MAX_SCALE = 0.75, 1.3


def lorem_pdf(pdf_bytes: bytes) -> bytes:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    for page in doc:
        spans = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                horizontal = abs(line["dir"][0] - 1) < 1e-3 and abs(line["dir"][1]) < 1e-3
                for span in line["spans"]:
                    if span["text"].strip():
                        spans.append((span, horizontal))
                        page.add_redact_annot(span["bbox"], fill=False)   # remove text, no cover box
        if not spans:
            continue
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
            text=pymupdf.PDF_REDACT_TEXT_REMOVE,
        )
        for span, horizontal in spans:
            if not horizontal:   # rotated text is removed, not rewritten
                continue
            text, font, size = lorem_text(span["text"]), _base14(span["font"], span["flags"]), span["size"]
            # Stretch/squeeze the lorem line to the original line's exact width, so centred,
            # right-aligned and justified text keeps its alignment and never overflows its box.
            natural = pymupdf.get_text_length(text, fontname=font, fontsize=size)
            target = span["bbox"][2] - span["bbox"][0]
            scale = max(_MIN_SCALE, min(_MAX_SCALE, target / natural)) if natural > 0 and target > 0 else 1.0
            origin = pymupdf.Point(span["origin"])
            page.insert_text(origin, text, fontsize=size, fontname=font, color=_rgb(span["color"]),
                             morph=(origin, pymupdf.Matrix(scale, 1)))
    doc.set_metadata({})
    doc.set_toc([])
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


# ── HTML / XML ────────────────────────────────────────────────────────────────

_STYLE_OR_SCRIPT_RE = re.compile(r"(<(style|script)\b.*?</\2>)", re.IGNORECASE | re.DOTALL)
_ENTITY_RE = re.compile(r"(&#?\w+;)")
_TEXT_ATTR_RE = re.compile(r"""(\s(?:alt|title|placeholder|aria-label)\s*=\s*)(["'])(.*?)\2""", re.IGNORECASE | re.DOTALL)


def _lorem_keep_entities(text: str) -> str:
    return "".join(part if _ENTITY_RE.fullmatch(part) else lorem_text(part) for part in _ENTITY_RE.split(text))


def lorem_html(markup: str) -> str:
    out = []
    for i, part in enumerate(_STYLE_OR_SCRIPT_RE.split(markup)):
        if i % 3 == 1:
            out.append(part)                  # <style>/<script> block untouched
        elif i % 3 == 0:
            part = re.sub(r">([^<]+)<", lambda m: ">" + _lorem_keep_entities(m.group(1)) + "<", ">" + part + "<")[1:-1]
            part = _TEXT_ATTR_RE.sub(lambda m: m.group(1) + m.group(2) + _lorem_keep_entities(m.group(3)) + m.group(2), part)
            out.append(part)
    return "".join(out)

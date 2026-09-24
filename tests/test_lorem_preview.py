"""core/lorem_preview.py — the saved-template preview shows the template's design
with every piece of its text swapped for lorem ipsum, so the original company's
content never shows in the picker. Generation still uses the real template."""

from __future__ import annotations

import base64
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from core.lorem_preview import lorem_html, lorem_pdf, lorem_text

ORIGINAL_WORDS = {"kline", "paper", "mill", "supplies", "recycling", "maryland", "customers"}


# ── text ──────────────────────────────────────────────────────────────────────

def test_lorem_text_keeps_shape_but_not_words():
    original = "KLINE PAPER Mill Supplies, Inc. — serving Maryland customers since 1970."
    out = lorem_text(original)
    assert out != original
    assert not ORIGINAL_WORDS & set(re.findall(r"[a-z]+", out.lower()))
    assert len(out) <= len(original)
    assert out.split()[0].isupper() and out.split()[1].isupper()          # UPPERCASE stays UPPERCASE
    assert out.split()[2][0].isupper() and out.split()[2][1:].islower()   # Title case stays Title
    assert "0000" in out                                                   # 1970 -> 0000
    assert "," in out and "—" in out and out.endswith(".")                 # punctuation kept


def test_lorem_text_numbers_become_zeros_keeping_format():
    assert lorem_text("$35,000,000") == "$00,000,000"
    assert lorem_text("27.8%") == "00.0%"


def test_lorem_text_is_deterministic_and_keeps_whitespace():
    assert lorem_text("  About Us\n") == lorem_text("  About Us\n")
    assert lorem_text("  About Us\n").startswith("  ") and lorem_text("  About Us\n").endswith("\n")


# ── PDF ───────────────────────────────────────────────────────────────────────

def _pdf_with_design() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(0, 40, 595, 110), color=None, fill=(0.1, 0.3, 0.5))    # colour band
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
    pix.set_rect(pix.irect, (200, 50, 50))
    page.insert_image(pymupdf.Rect(450, 700, 530, 780), pixmap=pix)                   # an image
    page.insert_text((50, 85), "KLINE PAPER MILL SUPPLIES", fontsize=24, fontname="Helvetica-Bold", color=(1, 1, 1))
    page.insert_text((50, 140), "Serving Maryland recycling customers.", fontsize=11, fontname="Times-Roman")
    doc.set_metadata({"title": "Kline Paper CIM", "author": "Kline Paper"})
    doc.set_toc([[1, "Kline Paper Overview", 1]])
    out = doc.tobytes()
    doc.close()
    return out


def test_lorem_pdf_replaces_all_text_but_keeps_design():
    out = pymupdf.open(stream=lorem_pdf(_pdf_with_design()), filetype="pdf")
    page = out[0]
    words = set(re.findall(r"[a-z]+", page.get_text().lower()))
    assert not ORIGINAL_WORDS & words
    assert words                                              # lorem text was written
    assert len(page.get_images()) == 1                        # image kept
    assert any(d.get("fill") for d in page.get_drawings())    # colour band kept
    assert not any(d.get("fill") == (1.0, 1.0, 1.0) for d in page.get_drawings())   # no white cover boxes
    spans = [s for b in page.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]]
    heading = max(spans, key=lambda s: s["bbox"][3] - s["bbox"][1])
    # Lines are stretched horizontally to the original width, which changes PyMuPDF's
    # reported "size" — the visible letter height is what must stay the same.
    original = max(_spans(_pdf_with_design()), key=lambda s: s["bbox"][3] - s["bbox"][1])
    oh, nh = original["bbox"][3] - original["bbox"][1], heading["bbox"][3] - heading["bbox"][1]
    assert abs(nh - oh) / oh < 0.1 and heading["color"] == 0xFFFFFF       # same height + colour
    assert "kline" not in str(out.metadata).lower()
    assert out.get_toc() == []


def test_lorem_pdf_keeps_page_count():
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page().insert_text((50, 72), f"Kline Paper page {i}", fontsize=12)
    out = pymupdf.open(stream=lorem_pdf(doc.tobytes()), filetype="pdf")
    assert out.page_count == 3


# ── HTML / XML ────────────────────────────────────────────────────────────────

def test_lorem_html_replaces_text_keeps_markup_and_css():
    html = ('<html><head><title>Kline Paper CIM</title><style>h1{color:#123456}</style></head>'
            '<body><h1 class="hero">KLINE PAPER</h1><p>Serving Maryland customers.</p>'
            '<img src="x.png" alt="Kline Paper mill"></body></html>')
    out = lorem_html(html)
    assert "<style>h1{color:#123456}</style>" in out
    assert '<h1 class="hero">' in out and '<img src="x.png"' in out
    assert not ORIGINAL_WORDS & set(re.findall(r"[a-z]+", re.sub(r"<[^>]+>", " ", out).lower()))
    assert "kline" not in out.lower()          # <title> and alt text too


# ── the preview endpoint ──────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import server
    server._lorem_preview_cache.clear()
    return TestClient(server.app)


def _saved(file_bytes: bytes, ext: str) -> dict:
    return {"file_b64": base64.standard_b64encode(file_bytes).decode(), "file_ext": ext}


def test_preview_endpoint_serves_lorem_pdf(client):
    with patch("server.template_store.get_template", AsyncMock(return_value=_saved(_pdf_with_design(), "pdf"))):
        r = client.get("/template/saved/1/preview")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    text = pymupdf.open(stream=r.content, filetype="pdf")[0].get_text().lower()
    assert "kline" not in text and "maryland" not in text


def test_preview_endpoint_serves_lorem_html(client):
    html = b"<html><body><h1>KLINE PAPER</h1></body></html>"
    with patch("server.template_store.get_template", AsyncMock(return_value=_saved(html, "html"))):
        r = client.get("/template/saved/1/preview")
    assert r.status_code == 200 and "kline" not in r.text.lower() and "<h1>" in r.text


def test_old_saved_docx_is_rendered_to_pdf_then_lorem(client):
    from tests.template_helpers import make_text_docx
    with patch("server.template_store.get_template", AsyncMock(return_value=_saved(make_text_docx(heading="Kline Paper"), "docx"))), \
         patch("server.convert_to_pdf", return_value=_pdf_with_design()) as render:
        r = client.get("/template/saved/1/preview")
    render.assert_called_once()
    assert r.headers["content-type"] == "application/pdf"
    assert "kline" not in pymupdf.open(stream=r.content, filetype="pdf")[0].get_text().lower()


def test_old_saved_docx_render_failure_falls_back_to_lorem_text(client):
    from core.office_convert import LegacyConversionError
    from tests.template_helpers import make_text_docx
    with patch("server.template_store.get_template", AsyncMock(return_value=_saved(make_text_docx(heading="Kline Paper"), "docx"))), \
         patch("server.convert_to_pdf", side_effect=LegacyConversionError("no soffice")):
        r = client.get("/template/saved/1/preview")
    assert r.status_code == 200 and "kline" not in r.text.lower()


def test_preview_is_built_once_then_served_from_memory(client):
    saved = _saved(_pdf_with_design(), "pdf")
    with patch("server.template_store.get_template", AsyncMock(return_value=saved)), \
         patch("server.lorem_pdf", wraps=lorem_pdf) as build:
        client.get("/template/saved/1/preview")
        client.get("/template/saved/1/preview")
    assert build.call_count == 1


def _spans(pdf: bytes) -> list[dict]:
    page = pymupdf.open(stream=pdf, filetype="pdf")[0]
    return [s for b in page.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"] if s["text"].strip()]


def test_lorem_line_has_same_width_as_original_so_alignment_is_kept():
    # A centred title: same width + same start => still centred.
    doc = pymupdf.open()
    page = doc.new_page()
    title = "KLINE PAPER MILL SUPPLIES, INC."
    w = pymupdf.get_text_length(title, fontname="helv", fontsize=20)
    page.insert_text(((595 - w) / 2, 300), title, fontsize=20, fontname="helv")
    original = _spans(doc.tobytes())[0]
    new = _spans(lorem_pdf(doc.tobytes()))[0]
    ow, nw = original["bbox"][2] - original["bbox"][0], new["bbox"][2] - new["bbox"][0]
    assert abs(nw - ow) / ow < 0.05
    assert abs(new["bbox"][0] - original["bbox"][0]) < 1


@pytest.mark.parametrize("font_name, expected", [
    ("Montserrat-Regular", "helv"), ("Carlito-Bold", "hebo"), ("ABCDEF+Calibri", "helv"),
    ("TimesNewRomanPS-BoldMT", "tibo"), ("Georgia-Italic", "tiit"), ("Caladea", "tiro"),
    ("CourierNewPSMT", "cour"), ("LiberationSans", "helv"),
])
def test_font_is_chosen_from_the_font_name(font_name, expected):
    from core.lorem_preview import _base14
    bold = "Bold" in font_name
    italic = "Italic" in font_name
    # flags deliberately claim "serif" (bit 4) — real PDFs often get this wrong
    flags = 4 | (16 if bold else 0) | (2 if italic else 0)
    assert _base14(font_name, flags) == expected


def test_lorem_words_match_original_word_length_when_possible():
    # Exact length first -> a lorem line needs almost no stretching to fit (no "bold" look).
    for word in ["Deserunt", "company", "the", "Maryland", "Paper"]:
        assert len(lorem_text(word)) == len(word)


def test_stretching_is_capped_so_text_never_looks_distorted():
    from core import lorem_preview
    assert lorem_preview._MIN_SCALE >= 0.7 and lorem_preview._MAX_SCALE <= 1.35

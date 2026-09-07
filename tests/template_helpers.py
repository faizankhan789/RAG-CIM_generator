"""Shared helpers for building small in-memory DOCX/HTML/XML files for
extractor/endpoint tests — mirrors tests/pdf_helpers.py's approach so tests
stay fully offline with no binary fixture files committed to the repo.
"""

from __future__ import annotations

import io


def make_text_docx(
    heading: str = "Executive Summary",
    body: str = "Some body copy about the business.",
    band_fill: str | None = None,
) -> bytes:
    """A minimal .docx with a styled Heading 1 + a body paragraph, optionally
    with a shaded table cell (band-color detection)."""
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import RGBColor

    doc = Document()
    heading_p = doc.add_paragraph(heading)
    heading_p.style = doc.styles["Heading 1"]
    heading_p.runs[0].font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
    heading_p.runs[0].font.name = "Georgia"

    body_p = doc.add_paragraph(body)
    body_p.runs[0].font.name = "Arial"

    if band_fill:
        table = doc.add_table(rows=1, cols=1)
        table.rows[0].cells[0].text = "Sample"
        tcPr = table.rows[0].cells[0]._tc.get_or_add_tcPr()
        shd = tcPr.makeelement(qn("w:shd"), {qn("w:fill"): band_fill})
        tcPr.append(shd)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_empty_docx() -> bytes:
    """A .docx with zero text runs — simulates a blank/unreadable document."""
    from docx import Document

    doc = Document()
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_text_html(
    heading: str = "Executive Summary",
    body: str = "Some body copy about the business.",
) -> bytes:
    """A minimal HTML page with a <style> block defining heading/body/band colors."""
    html = f"""<html><head><style>
h1 {{ color: #8B0000; font-family: Georgia, serif; text-align: center; }}
.band {{ background-color: #F4E4C1; }}
p {{ color: #333333; font-family: Arial, sans-serif; }}
</style></head><body>
<h1 class="band">{heading}</h1>
<p>{body}</p>
</body></html>"""
    return html.encode("utf-8")


def make_empty_html() -> bytes:
    return b"<html><head></head><body></body></html>"


def make_custom_schema_xml() -> bytes:
    """A hand-rolled XML schema with real text in non-HTML tags — no h1/p/etc,
    exercises the flat-text fallback path (no design hints found, degrades to
    defaults rather than erroring on a genuinely non-empty file)."""
    return (
        b"<template><section name='Executive Summary'>"
        b"Some body copy about the business.</section></template>"
    )

"""Shared helpers for building small in-memory PDFs for extractor/endpoint tests.

Built with PyMuPDF itself so tests are fully offline and don't need binary
fixture files committed to the repo.
"""

from __future__ import annotations

import pymupdf as fitz


def make_text_pdf(
    heading: str = "I. Executive Summary",
    body: str = "Some body copy about the business.",
) -> bytes:
    """A single-page PDF with one heading-sized span and one body-sized span."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), heading, fontsize=24, fontname="Helvetica-Bold", color=(0, 0, 1))
    page.insert_text((50, 110), body, fontsize=11, fontname="Helvetica", color=(0, 0, 0))
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def make_text_pdf_with_band(fill_rgb: tuple[float, float, float] = (1, 0.6, 0)) -> bytes:
    """A single-page PDF with a filled band behind the heading, for band-color detection."""
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(0, 60, 595, 100), color=None, fill=fill_rgb)
    page.insert_text((50, 85), "I. Executive Summary", fontsize=24, fontname="Helvetica-Bold", color=(0, 0, 1))
    page.insert_text((50, 130), "Some body copy about the business.", fontsize=11, fontname="Helvetica", color=(0, 0, 0))
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def make_no_text_pdf() -> bytes:
    """A page with a shape but zero text spans — simulates a scanned/image-only page."""
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(10, 10, 100, 100), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes

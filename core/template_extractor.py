"""Dispatches an uploaded template file to the right deterministic style
extractor by file extension. One entry point for server.py's
/template/upload, regardless of which format the user uploaded.

- .pdf            -> core/pdf_style_extractor.py    (PyMuPDF, page/bbox geometry)
- .docx / .doc    -> core/docx_style_extractor.py   (python-docx, paragraph/run
                     object model — legacy binary .doc can't be opened by
                     python-docx and is reported as UnsupportedTemplateFileError)
- .html/.htm/.xml -> core/markup_style_extractor.py (regex CSS/inline-style
                     scan, best-effort, no CSS cascade resolution)

No LLM in any of the above. The only place an LLM (Claude) ever sees the
uploaded file is core/llm.py:generate_cim_html, where it's attached as a raw
reference (vision for PDF, plain text otherwise) for visual/structural
mimicry only — never for style extraction.
"""

from __future__ import annotations

from core.pdf_style_extractor import NoExtractableTextError
from core.pdf_style_extractor import extract_style_profile as _extract_pdf

SUPPORTED_EXTENSIONS = {"pdf", "docx", "doc", "html", "htm", "xml"}


class UnsupportedTemplateFileError(ValueError):
    """Raised for an unsupported file extension, or a Word file python-docx
    can't open (legacy pre-2007 binary .doc, corruption, password-protection)."""


def extract_style_profile(file_bytes: bytes, filename: str) -> tuple[dict, list[str]]:
    """Extract a CIM template-style dict from an uploaded file, any supported
    format. Deterministic, no LLM. Returns (template_dict, warnings) — see
    core/pdf_style_extractor.py:extract_style_profile's docstring for the
    exact shape and warnings semantics, identical across every format.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else ""

    if ext == "pdf":
        return _extract_pdf(file_bytes)

    if ext in ("docx", "doc"):
        from core.docx_style_extractor import extract_style_profile as _extract_docx
        try:
            return _extract_docx(file_bytes)
        except NoExtractableTextError:
            raise
        except Exception as exc:
            raise UnsupportedTemplateFileError(
                "Couldn't read this Word document — it may be a legacy .doc file, "
                "corrupted, or password-protected. Please save it as .docx or PDF and re-upload."
            ) from exc

    if ext in ("html", "htm", "xml"):
        from core.markup_style_extractor import extract_style_profile as _extract_markup
        return _extract_markup(file_bytes)

    raise UnsupportedTemplateFileError(
        f"Unsupported template file type: .{ext or '?'}. "
        "Upload a PDF, Word (.docx), HTML, or XML file."
    )

"""Legacy .doc/.ppt -> .docx/.pptx conversion via headless LibreOffice.

python-docx and python-pptx can only open the modern OOXML formats
(.docx/.pptx) — legacy binary .doc/.ppt (pre-2007 Word/PowerPoint) raise on
open. No Python package reads the legacy binary formats (it's a full
document format, not something a library re-implements); LibreOffice's own
`soffice --headless --convert-to` can read them and re-save as the modern
format, so it's invoked as a subprocess. This is why the dependency lives in
the Dockerfile's apt-get step (libreoffice-writer/-impress), not requirements.txt
— it's a system application, not a pip package.

Used by every place in this codebase that opens a legacy .doc/.ppt file:
- server.py's /template/upload (custom CIM design template upload)
- nodes/word.py (data-room listing document ingestion)
- nodes/ppt.py (data-room listing document ingestion)

convert_to_pdf() also renders a modern .docx/.pptx template to PDF at upload
(server.py) — Claude's document vision only accepts PDF.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile

log = logging.getLogger(__name__)

_TARGET_FORMAT = {"doc": "docx", "ppt": "pptx"}
_PDF_SOURCE_EXTS = ("docx", "pptx")
_TIMEOUT_SECONDS = 60

# Real legacy .doc/.ppt files are OLE2 compound documents and always start with
# this signature. Checked BEFORE invoking soffice because LibreOffice Writer never
# rejects a .doc: anything it can't parse (plain text, random binary, an empty
# file) is silently "recovered" as plain text and converted successfully — so a
# junk file named .doc would otherwise become a bogus template instead of a 400.
_OLE2_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
# Modern OOXML (.docx/.pptx) is a zip package. A modern file that was merely
# renamed to .doc/.ppt needs no conversion at all — python-docx/python-pptx
# open it directly.
_ZIP_SIGNATURE = b"PK\x03\x04"


class LegacyConversionError(Exception):
    """Raised when a soffice conversion (legacy .doc/.ppt, or docx/pptx -> PDF) fails — every
    caller turns this into its own clear user-facing error / fallback
    rather than a raw subprocess failure."""


def convert_legacy(file_bytes: bytes, ext: str) -> tuple[bytes, str]:
    """Convert legacy .doc/.ppt bytes to modern .docx/.pptx bytes via a
    headless LibreOffice subprocess. Returns (converted_bytes, new_ext).

    Synchronous and blocking (a real subprocess call, up to _TIMEOUT_SECONDS)
    — callers on the asyncio event loop (server.py) must run this via
    run_in_executor; callers already inside a worker thread (nodes/word.py,
    nodes/ppt.py, both already wrapped in asyncio.to_thread by their caller)
    can call it directly.

    Each call gets its own LibreOffice user profile (-env:UserInstallation)
    in a fresh temp dir — soffice's default profile is not safe for
    concurrent invocations (a second concurrent call against the same
    profile directory fails/hangs waiting for the first one's lock).
    """
    target_ext = _TARGET_FORMAT.get(ext)
    if target_ext is None:
        raise LegacyConversionError(f"No conversion target configured for .{ext}")

    if file_bytes.startswith(_ZIP_SIGNATURE):
        log.info("office_convert: .%s is already a modern .%s package — no conversion needed", ext, target_ext)
        return file_bytes, target_ext
    if not file_bytes.startswith(_OLE2_SIGNATURE):
        raise LegacyConversionError(
            f"Not a real legacy .{ext} file (missing the Office binary signature)."
        )

    converted = _run_soffice(file_bytes, ext, target_ext)
    log.info("office_convert: .%s -> .%s (%d -> %d bytes)", ext, target_ext, len(file_bytes), len(converted))
    return converted, target_ext


def convert_to_pdf(file_bytes: bytes, ext: str) -> bytes:
    """Render a modern .docx/.pptx to PDF via headless LibreOffice.

    Used at template-upload time (server.py) so Claude can actually SEE a
    Word/PowerPoint template's pages as a PDF document block — the only
    format Claude's document vision accepts. Without this, those templates
    reached Claude as flattened text plus a few embedded images, so their
    real layout was never visible. Same blocking/concurrency notes as
    convert_legacy.
    """
    if ext not in _PDF_SOURCE_EXTS:
        raise LegacyConversionError(f"No PDF conversion configured for .{ext}")
    pdf = _run_soffice(file_bytes, ext, "pdf")
    if not pdf.startswith(b"%PDF"):
        raise LegacyConversionError("LibreOffice produced output that is not a PDF.")
    log.info("office_convert: .%s -> .pdf (%d -> %d bytes)", ext, len(file_bytes), len(pdf))
    return pdf


def _run_soffice(file_bytes: bytes, src_ext: str, target_ext: str) -> bytes:
    """One headless soffice --convert-to run in a throwaway temp dir with its
    own user profile. Returns the converted file's bytes."""
    with tempfile.TemporaryDirectory(prefix="office_convert_") as tmpdir:
        src_path = os.path.join(tmpdir, f"input.{src_ext}")
        profile_dir = os.path.join(tmpdir, "profile")
        with open(src_path, "wb") as f:
            f.write(file_bytes)

        try:
            result = subprocess.run(
                [
                    "soffice", "--headless", "--norestore",
                    f"-env:UserInstallation=file://{profile_dir}",
                    "--convert-to", target_ext,
                    "--outdir", tmpdir,
                    src_path,
                ],
                capture_output=True,
                timeout=_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise LegacyConversionError(
                "LibreOffice (soffice) is not installed on this server."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise LegacyConversionError(
                f"LibreOffice conversion timed out after {_TIMEOUT_SECONDS}s."
            ) from exc

        out_path = os.path.join(tmpdir, f"input.{target_ext}")
        if result.returncode != 0 or not os.path.isfile(out_path):
            stderr = result.stderr.decode("utf-8", errors="ignore").strip() if result.stderr else ""
            raise LegacyConversionError(
                f"LibreOffice conversion failed (exit {result.returncode}): {stderr[:300]}"
            )

        with open(out_path, "rb") as f:
            return f.read()

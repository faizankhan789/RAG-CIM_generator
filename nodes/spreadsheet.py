"""Spreadsheet node — download → raw text dump → Claude."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from core.downloader import download_to_temp
from core.llm import extract_from_content
from core.timing import FileTimer
from models import ExtractionError, ExtractedContent, FileItem, FileType
from state import CIMState

log = logging.getLogger(__name__)


def _xls_to_text(path: Path) -> str:
    import xlrd
    wb = xlrd.open_workbook(str(path))
    lines = []
    for sheet in wb.sheets():
        lines.append(f"[Sheet: {sheet.name}]")
        for row_idx in range(sheet.nrows):
            lines.append("\t".join(str(sheet.cell_value(row_idx, c)) for c in range(sheet.ncols)))
    return "\n".join(lines)


def _xlsx_to_text(path: Path) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    lines = []
    for ws in wb.worksheets:
        lines.append(f"[Sheet: {ws.title}]")
        for row in ws.iter_rows(values_only=True):
            lines.append("\t".join("" if v is None else str(v) for v in row))
    return "\n".join(lines)


def _to_text(path: Path) -> str:
    suffix = path.suffix.lower()

    if suffix in (".csv", ".tsv"):
        return path.read_text(errors="replace")

    if suffix == ".xls":
        return _xls_to_text(path)

    try:
        return _xlsx_to_text(path)
    except Exception:
        pass
    try:
        return _xls_to_text(path)
    except Exception:
        pass
    return path.read_text(errors="replace")


async def _process_one(item: FileItem, listing_xml: str = "") -> ExtractedContent:
    label = item.label or item.url.split("/")[-1].split("?")[0]
    try:
        with FileTimer("spreadsheet", label):
            if item.content:
                import tempfile, base64 as _b64
                suffix = "." + item.label.rsplit(".", 1)[-1].lower() if "." in item.label else ""
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(_b64.b64decode(item.content))
                    tmp_path = Path(tmp.name)
                try:
                    raw_text = await asyncio.to_thread(_to_text, tmp_path)
                finally:
                    tmp_path.unlink(missing_ok=True)
            else:
                async with download_to_temp(item.url) as path:
                    raw_text = await asyncio.to_thread(_to_text, path)

            content_blocks = [{"type": "text", "text": raw_text}]
            text = await extract_from_content(content_blocks, item.url, listing_xml)
        log.debug("  Spreadsheet extracted: %r", label)
        return ExtractedContent(
            source_url=item.url,
            file_type=FileType.SPREADSHEET,
            metadata={"extracted_text": text},
        )
    except Exception as exc:
        log.error("  Spreadsheet failed: %r — %s", label, exc)
        return ExtractedContent(source_url=item.url, file_type=FileType.SPREADSHEET, error=str(exc))


async def spreadsheet_node(state: CIMState) -> dict:
    files: list[FileItem] = state.get("spreadsheet_files", [])
    if not files:
        return {"extracted": [], "errors": []}

    log.debug("Spreadsheet: processing %d file(s)", len(files))
    results = await asyncio.gather(*[_process_one(f, state.get("listing_xml", "")) for f in files])

    extracted = [r for r in results if not r.error]
    errors = [ExtractionError(url=r.source_url, stage="spreadsheet", message=r.error) for r in results if r.error]
    if errors:
        log.error("Spreadsheet: %d error(s)", len(errors))
    return {"extracted": list(extracted), "errors": errors}

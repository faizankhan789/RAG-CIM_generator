"""PDF node — download → base64 → Claude."""

from __future__ import annotations

import asyncio
import base64
import logging

from core.downloader import download_to_temp
from core.llm import extract_from_content
from core.timing import FileTimer
from models import ExtractionError, ExtractedContent, FileItem, FileType
from state import CIMState

log = logging.getLogger(__name__)


async def _process_one(item: FileItem, listing_xml: str = "") -> ExtractedContent:
    label = item.label or item.url.split("/")[-1].split("?")[0]
    try:
        with FileTimer("pdf", label):
            if item.content:
                b64 = item.content
            else:
                async with download_to_temp(item.url) as path:
                    b64 = base64.standard_b64encode(path.read_bytes()).decode("utf-8")

            content_blocks = [
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
                }
            ]
            text = await extract_from_content(content_blocks, item.url, listing_xml)
        log.debug("  PDF extracted: %r", label)
        return ExtractedContent(
            source_url=item.url,
            file_type=FileType.PDF,
            metadata={"extracted_text": text},
        )
    except Exception as exc:
        log.error("  PDF failed: %r — %s", label, exc)
        return ExtractedContent(source_url=item.url, file_type=FileType.PDF, error=str(exc))


async def pdf_node(state: CIMState) -> dict:
    files: list[FileItem] = state.get("pdf_files", [])
    if not files:
        return {"extracted": [], "errors": []}

    log.debug("PDF: processing %d file(s)", len(files))
    results = await asyncio.gather(*[_process_one(f, state.get("listing_xml", "")) for f in files])

    extracted = [r for r in results if not r.error]
    errors = [ExtractionError(url=r.source_url, stage="pdf", message=r.error) for r in results if r.error]
    if errors:
        log.error("PDF: %d error(s)", len(errors))
    return {"extracted": list(extracted), "errors": errors}

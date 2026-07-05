"""Ingest node: flatten file tree and categorise by file type."""

from __future__ import annotations

import logging

from core.file_router import categorise, flatten_tree
from models import FileType
from state import CIMState

log = logging.getLogger(__name__)


async def ingest_node(state: CIMState) -> dict:
    tree = state.get("file_tree", {})
    all_files = flatten_tree(tree)
    by_type = categorise(all_files)

    pdf = by_type[FileType.PDF]
    sheets = by_type[FileType.SPREADSHEET]
    images = by_type[FileType.IMAGE]
    ppts = by_type[FileType.PPT]
    words = by_type.get(FileType.WORD, [])
    unknown = by_type.get(FileType.UNKNOWN, [])

    log.info(
        "Ingest: pdf=%d  spreadsheet=%d  image=%d  ppt=%d  word=%d  skipped=%d",
        len(pdf), len(sheets), len(images), len(ppts), len(words), len(unknown),
    )
    for label, bucket in [("PDF", pdf), ("Sheet", sheets), ("Image", images), ("PPT", ppts), ("Word", words)]:
        for f in bucket:
            log.info("  [%s] %s — %s", label, f.label or f.url.split("/")[-1].split("?")[0], f.url.split("?")[0])
    for f in unknown:
        log.info("  [SKIP] %s", f.url.split("?")[0])

    return {
        "pdf_files": pdf,
        "spreadsheet_files": sheets,
        "image_files": images,
        "ppt_files": ppts,
        "word_files": words,
        "extracted": [],
        "errors": [],
        "all_findings": [],
        "all_images": [],
        "cim_output": "",
    }

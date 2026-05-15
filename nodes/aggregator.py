"""Aggregator node — collect all per-file text findings and image data."""

from __future__ import annotations

import logging

from models import ExtractedContent, FileType
from state import CIMState

log = logging.getLogger(__name__)


async def aggregator_node(state: CIMState) -> dict:
    all_extracted: list[ExtractedContent] = state.get("extracted", [])

    findings = [c.metadata.get("extracted_text", "") for c in all_extracted if c.metadata.get("extracted_text")]
    images = [
        {"b64": c.metadata["image_b64"], "mime": c.metadata.get("image_mime", "image/jpeg"), "label": c.metadata.get("image_label", "")}
        for c in all_extracted
        if c.file_type == FileType.IMAGE and c.metadata.get("image_b64")
    ]

    log.info("Aggregator: %d finding(s), %d image(s) ready", len(findings), len(images))
    return {"all_findings": findings, "all_images": images}

"""Formatter node — Claude generates dynamic HTML CIM from all findings."""

from __future__ import annotations

import logging

from core.llm import generate_cim_html
from state import CIMState

log = logging.getLogger(__name__)


async def formatter_node(state: CIMState) -> dict:
    all_findings: list[str] = state.get("all_findings", [])
    all_images: list[dict] = state.get("all_images", [])
    listing_xml: str = state.get("listing_xml", "")
    listing_name: str = state.get("listing_name", "")
    asking_price: str = state.get("asking_price", "")

    if not all_findings and not listing_xml:
        log.error("Formatter: no findings and no listing context — cannot generate CIM")
        return {"cim_output": "<html><body><p>No content available to generate CIM.</p></body></html>"}

    log.info("Formatter: generating CIM HTML (findings=%d, images=%d)", len(all_findings), len(all_images))
    html = await generate_cim_html(all_findings, listing_xml, listing_name, asking_price, all_images)
    log.info("Formatter: done — %.1f KB", len(html) / 1024)
    return {"cim_output": html}

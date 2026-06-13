"""Formatter node — Claude generates dynamic HTML CIM from all findings."""

from __future__ import annotations

import base64
import logging

import httpx

from core.llm import generate_cim_html
from state import CIMState

log = logging.getLogger(__name__)


async def _fetch_logo(url: str) -> tuple[str, str]:
    """Download logo URL → (base64_string, mime_type). Returns ('', '') on failure."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30, verify=False) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            mime = resp.headers.get("content-type", "image/png").split(";")[0].strip()
            b64 = base64.standard_b64encode(resp.content).decode()
            log.info("Formatter: logo fetched (%s, %.1f KB)", mime, len(resp.content) / 1024)
            return b64, mime
    except Exception as exc:
        log.error("Formatter: logo fetch failed — %s", exc)
        return "", ""


async def formatter_node(state: CIMState) -> dict:
    all_findings: list[str] = state.get("all_findings", [])
    all_images: list[dict] = state.get("all_images", [])
    listing_xml: str = state.get("listing_xml", "")
    listing_name: str = state.get("listing_name", "")
    asking_price: str = state.get("asking_price", "")
    logo_url: str = state.get("logo_url", "")

    if not all_findings and not listing_xml:
        log.error("Formatter: no findings and no listing context — cannot generate CIM")
        return {"cim_output": "<html><body><p>No content available to generate CIM.</p></body></html>"}

    logo_b64, logo_mime = "", ""
    if logo_url:
        logo_b64, logo_mime = await _fetch_logo(logo_url)

    log.info("Formatter: generating CIM HTML (findings=%d, images=%d, logo=%s)", len(all_findings), len(all_images), bool(logo_b64))
    html = await generate_cim_html(all_findings, listing_xml, listing_name, asking_price, all_images, logo_b64=logo_b64, logo_mime=logo_mime)
    log.info("Formatter: done — %.1f KB", len(html) / 1024)
    return {"cim_output": html}

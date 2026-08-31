"""Formatter node — Claude generates dynamic HTML CIM from all findings."""

from __future__ import annotations

import base64
import io
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
            log.debug("Formatter: logo fetched (%s, %.1f KB)", mime, len(resp.content) / 1024)
            return b64, mime
    except Exception as exc:
        log.error("Formatter: logo fetch failed — %s", exc)
        return "", ""


def _extract_brand_colors(b64: str, mime: str) -> tuple[str, str]:
    """Extract dominant + accent hex colors from logo via Pillow. Returns ('', '') on failure."""
    try:
        from PIL import Image
        raw = base64.standard_b64decode(b64)
        img = Image.open(io.BytesIO(raw)).convert("RGB").resize((120, 120))
        colors = img.getcolors(maxcolors=14400)
        if not colors:
            return "", ""

        def brightness(rgb: tuple) -> float:
            return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]

        def diff(a: tuple, b: tuple) -> int:
            return sum(abs(x - y) for x, y in zip(a, b))

        def to_hex(rgb: tuple) -> str:
            return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"

        usable = sorted(
            [(cnt, rgb) for cnt, rgb in colors if 18 < brightness(rgb) < 237],
            reverse=True,
        )
        if not usable:
            return "", ""

        primary = usable[0][1]

        accent = None
        for _, rgb in usable[1:]:
            if diff(primary, rgb) > 75:
                accent = rgb
                break

        if not accent:
            br = brightness(primary)
            if br < 110:
                accent = (185, 144, 50)  # gold fallback for dark logos
            else:
                accent = tuple(max(0, c - 75) for c in primary)

        log.debug("Formatter: brand colors extracted — primary=%s accent=%s", to_hex(primary), to_hex(accent))
        return to_hex(primary), to_hex(accent)
    except Exception as exc:
        log.error("Formatter: brand color extraction failed — %s", exc)
        return "", ""


async def formatter_node(state: CIMState) -> dict:
    all_findings: list[str] = state.get("all_findings", [])
    all_images: list[dict] = state.get("all_images", [])
    listing_xml: str = state.get("listing_xml", "")
    listing_name: str = state.get("listing_name", "")
    asking_price: str = state.get("asking_price", "")
    logo_url: str = state.get("logo_url", "")
    template_id: str = state.get("template_id", "classic")
    custom_template: dict | None = state.get("custom_template")

    if not all_findings and not listing_xml:
        log.error("Formatter: no findings and no listing context — cannot generate CIM")
        return {"cim_output": "<html><body><p>No content available to generate CIM.</p></body></html>"}

    logo_b64, logo_mime = "", ""
    brand_primary, brand_accent = "", ""
    if logo_url:
        logo_b64, logo_mime = await _fetch_logo(logo_url)
        if logo_b64:
            brand_primary, brand_accent = _extract_brand_colors(logo_b64, logo_mime)

    log.info(
        "Formatter: generating CIM HTML (findings=%d, images=%d, logo=%s, brand=%s)",
        len(all_findings), len(all_images), bool(logo_b64), brand_primary or "none",
    )
    html = await generate_cim_html(
        all_findings, listing_xml, listing_name, asking_price, all_images,
        logo_b64=logo_b64, logo_mime=logo_mime,
        brand_primary=brand_primary, brand_accent=brand_accent,
        template_id=template_id,
        custom_template=custom_template,
    )
    log.info("Formatter: done — %.1f KB", len(html) / 1024)
    return {"cim_output": html}

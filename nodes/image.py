"""Image node — validate → base64 → Claude vision."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import mimetypes

from core.downloader import download_to_temp
from core.llm import extract_from_content
from core.timing import FileTimer
from models import ExtractionError, ExtractedContent, FileItem, FileType
from state import CIMState

log = logging.getLogger(__name__)

_SUPPORTED_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_DEFAULT_MIME = "image/jpeg"
_MIN_DIM = 32


def _guess_mime(path_str: str) -> str:
    mime, _ = mimetypes.guess_type(path_str)
    if mime in _SUPPORTED_MIME:
        return mime
    if mime in ("image/tiff", "image/bmp"):
        return "image/png"
    return _DEFAULT_MIME


def _to_png_bytes(raw_bytes: bytes) -> bytes:
    from PIL import Image
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _validate_image(image_bytes: bytes, label: str) -> tuple[bool, str]:
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        img.verify()
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        if w < _MIN_DIM or h < _MIN_DIM:
            return False, f"too small ({w}x{h})"
        return True, ""
    except Exception as exc:
        return False, str(exc)


async def _load_image(item: FileItem) -> tuple[bytes, str]:
    """Returns (image_bytes, mime). Tries disk content first, falls back to URL download."""
    if item.content:
        try:
            mime = _guess_mime(item.label)
            image_bytes = base64.standard_b64decode(item.content)
            if image_bytes:
                valid, _ = _validate_image(image_bytes, item.label)
                if valid:
                    return image_bytes, mime
        except Exception:
            pass
        if item.url:
            log.debug("  Image: disk content corrupt for %r, trying URL download", item.label)

    if not item.url:
        raise ValueError("no valid content and no URL")

    async with download_to_temp(item.url, timeout=10) as path:
        image_bytes = path.read_bytes()
        # Gallery URLs sometimes return HTML redirect pages — reject fast
        if image_bytes[:100].lstrip().startswith((b"<", b"<!DOCTYPE", b"<!doctype")):
            raise ValueError("URL returned HTML, not an image")
        mime = _guess_mime(str(path))
        return image_bytes, mime


async def _process_one(item: FileItem, listing_xml: str = "") -> ExtractedContent:
    label = item.label or item.url.split("/")[-1].split("?")[0]
    try:
        with FileTimer("image", label):
            image_bytes, mime = await _load_image(item)

            if mime == "image/png" and not item.label.lower().endswith(".png"):
                image_bytes = await asyncio.to_thread(_to_png_bytes, image_bytes)

            b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

            valid, reason = _validate_image(image_bytes, label)
            if not valid:
                log.error("  Image skipped: %r — %s", label, reason)
                return ExtractedContent(source_url=item.url, file_type=FileType.IMAGE, error=f"skipped: {reason}")

            content_blocks = [
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}
            ]
            text = await extract_from_content(content_blocks, item.url, listing_xml)

        log.debug("  Image extracted: %r", label)
        return ExtractedContent(
            source_url=item.url,
            file_type=FileType.IMAGE,
            metadata={
                "extracted_text": text,
                "image_b64": b64,
                "image_mime": mime,
                "image_label": label,
            },
        )
    except Exception as exc:
        log.error("  Image failed: %r — %s", label, exc)
        return ExtractedContent(source_url=item.url, file_type=FileType.IMAGE, error=str(exc))


async def image_node(state: CIMState) -> dict:
    files: list[FileItem] = state.get("image_files", [])
    if not files:
        return {"extracted": [], "errors": []}

    log.debug("Image: processing %d file(s)", len(files))
    results = await asyncio.gather(*[_process_one(f, state.get("listing_xml", "")) for f in files])

    extracted = [r for r in results if not r.error]
    errors = [ExtractionError(url=r.source_url, stage="image", message=r.error) for r in results if r.error]
    if errors:
        log.error("Image: %d error(s)", len(errors))
    return {"extracted": list(extracted), "errors": errors}

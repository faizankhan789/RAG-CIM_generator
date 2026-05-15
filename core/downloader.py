"""Streaming file downloader — memory-safe for large files."""

from __future__ import annotations

import asyncio
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import httpx

_MAX_BYTES: int = int(os.getenv("MAX_FILE_BYTES", str(2 * 1024 ** 3)))  # 2 GB default
_CONCURRENCY: int = int(os.getenv("MAX_DOWNLOAD_CONCURRENCY", "5"))
_CHUNK: int = 256 * 1024  # 256 KB read chunks

# Semaphore is created lazily per event-loop to avoid cross-loop issues
_sem: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(_CONCURRENCY)
    return _sem


def _build_headers() -> dict[str, str]:
    auth = os.getenv("FILE_AUTH_HEADER", "").strip()
    return {"Authorization": auth} if auth else {}


@asynccontextmanager
async def download_to_temp(url: str) -> AsyncIterator[Path]:
    """
    Stream-download *url* to a temporary file.
    Yields the Path; deletes on exit.
    Raises httpx.HTTPStatusError for non-2xx responses.
    Raises ValueError if file exceeds MAX_FILE_BYTES.
    """
    headers = _build_headers()
    sem = _get_semaphore()

    async with sem:
        # verify=False handles self-signed/weak certs on local CRM installs;
        # in production replace with verify=True or a custom CA bundle
        async with httpx.AsyncClient(follow_redirects=True, timeout=120, verify=False) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                resp.raise_for_status()
                suffix = _suffix_from_response(url, resp)
                with tempfile.NamedTemporaryFile(
                    suffix=suffix, delete=False
                ) as tmp:
                    tmp_path = Path(tmp.name)
                    written = 0
                    async for chunk in resp.aiter_bytes(_CHUNK):
                        written += len(chunk)
                        if written > _MAX_BYTES:
                            tmp_path.unlink(missing_ok=True)
                            raise ValueError(
                                f"File at {url!r} exceeds MAX_FILE_BYTES ({_MAX_BYTES})"
                            )
                        tmp.write(chunk)
    try:
        yield tmp_path
    finally:
        tmp_path.unlink(missing_ok=True)


def _suffix_from_response(url: str, resp: httpx.Response) -> str:
    ct = resp.headers.get("content-type", "")
    # Try to guess extension from content-disposition first
    cd = resp.headers.get("content-disposition", "")
    if "filename=" in cd:
        fname = cd.split("filename=")[-1].strip().strip('"')
        if "." in fname:
            return "." + fname.rsplit(".", 1)[-1].lower()

    # Fall back to URL path
    path_part = url.split("?")[0].split("#")[0]
    if "." in path_part.rsplit("/", 1)[-1]:
        return "." + path_part.rsplit(".", 1)[-1].lower()

    return ""

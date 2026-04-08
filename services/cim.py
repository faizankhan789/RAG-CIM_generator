"""
CIM generation pipeline
=======================

This module drives the full pipeline for building a CIM from a listing tree.
The pipeline runs in the following stages:

  1. Downloading
     All files in the listing are collected into a flat (FileItem, destination)
     list and submitted concurrently to the download pool. Each job streams the
     file to disk and detects its mime_type from the file header.

  2. Processing
     Once all downloads complete, every file is submitted to the process pool.
     Each file is routed to the appropriate processor based on its mime_type:
       - Readable documents (PDF, DOCX, TXT, PPTX, …) → chunked for embeddings
       - Tabular data (CSV, XLSX, XLS, ODS, …)        → tabular analysis
       - Images (JPEG, PNG, WEBP, …)                  → vision pipeline

  (Future stages: retrieval, generation, …)
"""

import asyncio
import logging
import os
import uuid

import httpx
import filetype

from core.config import settings
from core.mime_types import CHUNKABLE_MIME_TYPES, TABULAR_MIME_TYPES, IMAGE_MIME_TYPES
from core.pools import download_pool, process_pool
from models import FileItem, FolderItem
from models.listing import ListingFile

logger = logging.getLogger(__name__)

# Injected at startup by main.py lifespan. Shared across all workers for connection pooling.
_http_client: httpx.AsyncClient

# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _collect_downloads(
    items: list[ListingFile],
    destination: str,
) -> list[tuple[FileItem, str]]:
    """
    Recursively walks the listing tree and returns a flat list of
    (FileItem, destination_dir) pairs. Folder structure is reflected
    in the destination paths — no tasks are created here.
    """
    result: list[tuple[FileItem, str]] = []
    for item in items:
        if isinstance(item, FileItem):
            logger.debug("Collected file '%s' (url: %s) -> '%s'", item.name, item.url, destination)
            result.append((item, destination))
        elif isinstance(item, FolderItem):
            # Mirror the folder hierarchy under the destination directory.
            folder_path = os.path.join(destination, item.name)
            logger.debug("Traversing folder '%s' -> '%s'", item.name, folder_path)
            result.extend(_collect_downloads(item.children, folder_path))
    return result


async def _download_file(file: FileItem, destination: str) -> FileItem:
    """
    Downloads a single file into destination. If mime_type is absent,
    detects it from the response bytes and appends the correct extension
    when the filename has none. Returns an updated FileItem with
    local_path and mime_type set.
    """
    os.makedirs(destination, exist_ok=True)
    logger.debug("Downloading '%s' from %s", file.name, file.url)

    filename = file.name
    mime_type = file.mime_type

    try:
        async with _http_client.stream("GET", str(file.url)) as response:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "HTTP %s downloading '%s' (url: %s) — reason: %s",
                    exc.response.status_code, file.name, file.url, exc.response.reason_phrase,
                )
                raise

            # Stream chunks (64 KB each) directly to disk to avoid loading the full
            # file into memory. While streaming, we accumulate the opening bytes into
            # `header` solely for mime_type detection — filetype only needs 261 bytes
            # (the largest magic-byte signature it knows). Once we have enough bytes
            # or mime_type is resolved, header accumulation stops.
            header = b""
            local_path = os.path.join(destination, filename)

            with open(local_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=65536):  # 64 KB
                    if mime_type is None and len(header) < 261:
                        header += chunk
                        kind = filetype.guess(header[:261])
                        if kind is not None:
                            mime_type = kind.mime
                            _, existing_ext = os.path.splitext(filename)
                            if not existing_ext:
                                # Rename the partially-written file to include the detected
                                # extension. Safe mid-stream on Linux/macOS — the open file
                                # handle remains valid after rename.
                                filename = f"{filename}.{kind.extension}"
                                new_path = os.path.join(destination, filename)
                                os.rename(local_path, new_path)
                                local_path = new_path
                            logger.debug("Detected mime_type '%s' for '%s'", mime_type, file.name)
                    f.write(chunk)

            # Final check after the full stream — mime_type is still None only if
            # filetype could not match any known signature in the file header.
            if mime_type is None:
                logger.warning(
                    "Could not detect mime_type for '%s' (url: %s) — saved without extension",
                    file.name, file.url,
                )

    except httpx.RequestError as exc:
        logger.error(
            "Request failed for '%s' (url: %s) — reason: %s",
            file.name, file.url, exc,
        )
        raise

    logger.debug("Saved '%s' -> '%s'", file.name, local_path)
    return file.model_copy(update={"mime_type": mime_type, "local_path": local_path})


# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------

async def download_files(items: list[ListingFile]) -> list[FileItem]:
    """
    Enqueues all files from the listing tree into the global download pool.
    Files land in {CIM_DOWNLOAD_DIR}/{temp_name}/ where temp_name is unique
    per call. Returns updated FileItems with local_path and mime_type populated.
    """
    # Unique subdirectory per batch so concurrent requests never collide.
    temp_name = uuid.uuid4().hex
    destination = os.path.join(settings.cim_download_dir, temp_name)

    pairs = _collect_downloads(items, destination)
    if not pairs:
        logger.info("download_files called with no files — nothing to do")
        return []

    logger.info("Starting download batch '%s' — %d file(s) queued", temp_name, len(pairs))

    results = await asyncio.gather(
        *[download_pool.submit(_download_file(file, dest)) for file, dest in pairs]
    )

    logger.info("Download batch '%s' complete — %d file(s) saved", temp_name, len(results))
    return list(results)


# ---------------------------------------------------------------------------
# File type routing
# ---------------------------------------------------------------------------

async def _chunk_file(file: FileItem) -> None:
    """Process a readable document by chunking it for embeddings. (not yet implemented)"""
    logger.debug("Chunking file '%s' (mime_type: %s)", file.name, file.mime_type)
    # TODO: implement chunking and embedding generation.


async def _analyze_tabular(file: FileItem) -> None:
    """Process a tabular file with pandas or equivalent analysis. (not yet implemented)"""
    logger.debug("Analyzing tabular file '%s' (mime_type: %s)", file.name, file.mime_type)
    # TODO: implement tabular analysis.


async def _process_image(file: FileItem) -> None:
    """Process an image file through vision-based pipeline. (not yet implemented)"""
    logger.debug("Processing image '%s' (mime_type: %s)", file.name, file.mime_type)
    # TODO: implement image processing.


async def _route_file(file: FileItem) -> None:
    """Selects and runs the correct processor for a single file based on its mime_type."""
    mime = file.mime_type

    if mime in CHUNKABLE_MIME_TYPES:
        await _chunk_file(file)
    elif mime in TABULAR_MIME_TYPES:
        await _analyze_tabular(file)
    elif mime in IMAGE_MIME_TYPES:
        await _process_image(file)
    else:
        logger.warning(
            "No processor for file '%s' (mime_type: %s) — skipping",
            file.name, mime,
        )


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

async def _process_files(files: list[FileItem]) -> None:
    """
    Submits each downloaded file to the process pool for concurrent processing.
    Each file is routed to the appropriate processor based on its mime_type:
      - Readable documents (PDF, DOCX, TXT, PPTX, …) → _chunk_file
      - Tabular data (CSV, XLSX, XLS, ODS, …)        → _analyze_tabular
      - Images (JPEG, PNG, WEBP, …)                  → _process_image
      - Unrecognised types                            → skipped with a warning
    """
    await asyncio.gather(
        *[process_pool.submit(_route_file(file)) for file in files]
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def create_cim(items: list[ListingFile]) -> str:
    """
    Orchestrates the full CIM generation pipeline.
    Steps:
      1. Download all listing files.
      (future steps: chunking, retrieval, …)

    Returns the final HTML as a string.
    """
    downloaded = await download_files(items)
    logger.info("create_cim — %d file(s) downloaded, proceeding to next steps", len(downloaded))

    await _process_files(downloaded)

    # TODO: add retrieval and generation steps here.

    return ""



import asyncio
import logging
import os
import uuid

import httpx
import filetype

from core.config import settings
from core.pools import download_pool
from models import FileItem, FolderItem
from models.listing import ListingFile

logger = logging.getLogger(__name__)

# Injected at startup by main.py lifespan. Shared across all workers for connection pooling.
_http_client: httpx.AsyncClient

# ---------------------------------------------------------------------------
# Internal helpers
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
# Public API
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

import asyncio
import mimetypes
import os

import httpx
import filetype

from models import FileItem, FolderItem
from models.listing import ListingFile

MAX_CONCURRENT_DOWNLOADS = 10
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)


async def download_files(
    items: list[ListingFile],
    destination: str,
    client: httpx.AsyncClient,
) -> list[str]:
    """
    Recursively downloads all files from the listing tree concurrently.
    Folders become local directories; files are fetched via HTTP.
    """
    tasks: list[asyncio.Task] = []

    for item in items:
        if isinstance(item, FileItem):
            tasks.append(asyncio.create_task(_download_file(item, destination, client)))
        elif isinstance(item, FolderItem):
            folder_path = os.path.join(destination, item.name)
            tasks.append(asyncio.create_task(download_files(item.children, folder_path, client)))

    results = await asyncio.gather(*tasks)

    downloaded: list[str] = []
    for result in results:
        if isinstance(result, list):
            downloaded.extend(result)
        else:
            downloaded.append(result)
    return downloaded


async def _download_file(
    file: FileItem,
    destination: str,
    client: httpx.AsyncClient,
) -> str:
    """
    Downloads a single file. If mime_type is None, detects the real type
    from the downloaded content using libmagic (magic bytes) and appends
    the correct extension when the filename lacks one.
    """
    os.makedirs(destination, exist_ok=True)

    async with _semaphore:
        response = await client.get(str(file.url))
        response.raise_for_status()

    content = response.content
    filename = file.name

    if file.mime_type is None:
        kind = filetype.guess(content)
        _, existing_ext = os.path.splitext(filename)
        if not existing_ext and kind is not None:
            filename = f"{filename}.{kind.extension}"

    local_path = os.path.join(destination, filename)

    with open(local_path, "wb") as f:
        f.write(content)

    return local_path

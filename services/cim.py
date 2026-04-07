from models import FileItem, FolderItem
from models.listing import ListingFile


def download_files(items: list[ListingFile], destination: str) -> list[str]:
    """
    Recursively downloads all files from the listing to the given destination directory.
    Returns a list of downloaded file paths.
    """
    downloaded: list[str] = []
    for item in items:
        if isinstance(item, FileItem):
            path = _download_file(item, destination)
            downloaded.append(path)
        elif isinstance(item, FolderItem):
            downloaded.extend(download_files(item.children, f"{destination}/{item.name}"))
    return downloaded


def _download_file(file: FileItem, destination: str) -> str:
    """
    Downloads a single file from its URL to the destination directory.
    Returns the local path where the file was saved.
    """
    # TODO: implement actual download logic (e.g. httpx.get(file.url))
    local_path = f"{destination}/{file.name}"
    return local_path

"""MIME type → FileType mapping."""

from __future__ import annotations

from models import FileType

# Maps lowercase extension (no dot) → FileType
_EXT_MAP: dict[str, FileType] = {
    # PDF
    "pdf": FileType.PDF,
    # Spreadsheets
    "xlsx": FileType.SPREADSHEET,
    "xls": FileType.SPREADSHEET,
    "xlsm": FileType.SPREADSHEET,
    "xlsb": FileType.SPREADSHEET,
    "csv": FileType.SPREADSHEET,
    "ods": FileType.SPREADSHEET,
    "tsv": FileType.SPREADSHEET,
    # Images
    "png": FileType.IMAGE,
    "jpg": FileType.IMAGE,
    "jpeg": FileType.IMAGE,
    "tiff": FileType.IMAGE,
    "tif": FileType.IMAGE,
    "bmp": FileType.IMAGE,
    "webp": FileType.IMAGE,
    "gif": FileType.IMAGE,
    # PowerPoint
    "pptx": FileType.PPT,
    "ppt": FileType.PPT,
    "pptm": FileType.PPT,
    "odp": FileType.PPT,
    # Word
    "docx": FileType.WORD,
    "doc": FileType.WORD,
    "docm": FileType.WORD,
    "odt": FileType.WORD,
    "rtf": FileType.WORD,
    "txt": FileType.WORD,
}

# Maps content-type header → FileType (partial match on prefix)
_MIME_MAP: dict[str, FileType] = {
    "application/pdf": FileType.PDF,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": FileType.SPREADSHEET,
    "application/vnd.ms-excel": FileType.SPREADSHEET,
    "application/vnd.oasis.opendocument.spreadsheet": FileType.SPREADSHEET,
    "text/csv": FileType.SPREADSHEET,
    "image/png": FileType.IMAGE,
    "image/jpeg": FileType.IMAGE,
    "image/tiff": FileType.IMAGE,
    "image/bmp": FileType.IMAGE,
    "image/webp": FileType.IMAGE,
    "image/gif": FileType.IMAGE,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": FileType.PPT,
    "application/vnd.ms-powerpoint": FileType.PPT,
    "application/vnd.oasis.opendocument.presentation": FileType.PPT,
    # Word
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": FileType.WORD,
    "application/msword": FileType.WORD,
    "application/vnd.oasis.opendocument.text": FileType.WORD,
    "application/rtf": FileType.WORD,
    "text/rtf": FileType.WORD,
    "text/plain": FileType.WORD,
}


def file_type_from_url(url: str, content_type: str = "") -> FileType:
    """Derive FileType from URL extension, falling back to content-type header."""
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        if ct in _MIME_MAP:
            return _MIME_MAP[ct]

    # Extract extension from URL path (ignore query string)
    path_part = url.split("?")[0].split("#")[0]
    ext = path_part.rsplit(".", 1)[-1].lower() if "." in path_part else ""
    return _EXT_MAP.get(ext, FileType.UNKNOWN)

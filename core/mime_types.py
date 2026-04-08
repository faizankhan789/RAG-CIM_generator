# MIME types that contain readable text — suitable for chunking and embeddings.
CHUNKABLE_MIME_TYPES: frozenset[str] = frozenset({
    "application/pdf",
    "application/msword",                                                          # .doc
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",    # .docx
    "application/vnd.ms-powerpoint",                                              # .ppt
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
    "text/plain",
    "text/html",
    "text/markdown",
    "application/rtf",
})

# MIME types that contain tabular or structured data — suitable for pandas / analysis.
TABULAR_MIME_TYPES: frozenset[str] = frozenset({
    "text/csv",
    "application/vnd.ms-excel",                                                   # .xls
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",          # .xlsx
    "application/vnd.oasis.opendocument.spreadsheet",                             # .ods
})

# MIME types that are images — suitable for vision-based processing.
IMAGE_MIME_TYPES: frozenset[str] = frozenset({
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/tiff",
    "image/bmp",
})

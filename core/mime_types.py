# MIME types that contain readable text — suitable for chunking and embeddings.
CHUNKABLE_MIME_TYPES: frozenset[str] = frozenset({
    "application/pdf",
    "application/x-pdf",                                                          # .pdf (legacy)
    "application/msword",                                                          # .doc
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",    # .docx
    "application/vnd.ms-word.document.macroEnabled.12",                          # .docm
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template",   # .dotx
    "application/vnd.oasis.opendocument.text",                                   # .odt
    "application/vnd.ms-powerpoint",                                              # .ppt / .ppa
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
    "application/vnd.openxmlformats-officedocument.presentationml.slideshow",     # .ppsx
    "application/vnd.openxmlformats-officedocument.presentationml.template",      # .potx
    "application/vnd.ms-powerpoint.presentation.macroEnabled.12",                 # .pptm
    "application/vnd.ms-powerpoint.slideshow.macroEnabled.12",                    # .ppsm
    "application/vnd.ms-powerpoint.template.macroEnabled.12",                     # .potm
    "application/vnd.oasis.opendocument.presentation",                            # .odp
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

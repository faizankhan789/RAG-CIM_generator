from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import AnyHttpUrl, BaseModel, Field


class ListingData(BaseModel):
    """Metadata about the listing (id, code, name). Extra fields are allowed and preserved."""

    id: int
    code: str
    name: str

    model_config = {"extra": "allow"}


class FileItem(BaseModel):
    """A single downloadable file in the listing tree."""

    type: Literal["file"]
    name: str
    # None when the server does not provide it — detected from content bytes after download.
    mime_type: Optional[str] = None
    id: int
    # Unix timestamp of last modification.
    modified: int
    url: AnyHttpUrl
    # Populated after download with the absolute local path where the file was saved.
    local_path: Optional[str] = None


class FolderItem(BaseModel):
    """A folder in the listing tree. Children may be files or nested folders."""

    type: Literal["folder"]
    name: str
    children: list[ListingFile] = Field(default_factory=list)


# Discriminated union — Pydantic uses the "type" field to decide which model to parse into.
ListingFile = Annotated[
    Union[FileItem, FolderItem],
    Field(discriminator="type"),
]

# Required because FolderItem references ListingFile which is defined after FolderItem.
FolderItem.model_rebuild()


class ListingRequest(BaseModel):
    """Top-level request body for the /generate endpoint."""

    listing_data: ListingData
    listing_files: list[ListingFile] = Field(
        ...,
        description="List of files/folders. May be empty but must be present and not null.",
    )

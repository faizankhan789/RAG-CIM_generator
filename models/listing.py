from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import AnyHttpUrl, BaseModel, Field


class ListingData(BaseModel):
    id: int
    code: str
    name: str

    model_config = {"extra": "allow"}


class FileItem(BaseModel):
    type: Literal["file"]
    name: str
    mime_type: Optional[str] = None
    id: int
    modified: int
    url: AnyHttpUrl


class FolderItem(BaseModel):
    type: Literal["folder"]
    name: str
    children: list[ListingFile] = Field(default_factory=list)


ListingFile = Annotated[
    Union[FileItem, FolderItem],
    Field(discriminator="type"),
]

FolderItem.model_rebuild()


class ListingRequest(BaseModel):
    listing_data: ListingData
    listing_files: list[ListingFile] = Field(
        ...,
        description="List of files/folders. May be empty but must be present and not null.",
    )

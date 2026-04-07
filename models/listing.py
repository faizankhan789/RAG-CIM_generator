from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


class ListingData(BaseModel):
    id: str
    code: str

    model_config = {"extra": "allow"}


class FileItem(BaseModel):
    type: Literal["file"]
    name: str
    mime_type: Optional[str] = Field(None, alias="mimeType")
    id: str
    modified: int
    url: str

    @model_validator(mode="after")
    def check_mime_type(self) -> "FileItem":
        if self.mime_type is None:
            # mimeType was not provided — downstream handlers should detect/infer it
            pass
        return self

    model_config = {"populate_by_name": True}


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

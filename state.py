"""LangGraph state definition for the CIM pipeline."""

from __future__ import annotations

from typing import Annotated, Any

from typing_extensions import TypedDict

from models import ExtractionError, ExtractedContent, FileItem


def _merge_lists(a: list, b: list) -> list:
    return a + b


def _merge_errors(a: list[ExtractionError], b: list[ExtractionError]) -> list[ExtractionError]:
    return a + b


class CIMState(TypedDict):
    # Input
    file_tree: dict[str, Any]
    listing_xml: str
    listing_name: str
    asking_price: str

    # After ingest: categorised file lists
    pdf_files: list[FileItem]
    spreadsheet_files: list[FileItem]
    image_files: list[FileItem]
    ppt_files: list[FileItem]

    # After processor nodes: accumulated extracted content
    extracted: Annotated[list[ExtractedContent], _merge_lists]

    # Non-fatal errors from any node
    errors: Annotated[list[ExtractionError], _merge_errors]

    # After aggregator: list of per-file free-form findings texts
    all_findings: list[str]

    # After aggregator: list of images to potentially embed in HTML
    # Each: {"b64": str, "mime": str, "label": str}
    all_images: list[dict]

    # Final HTML output (set by formatter)
    cim_output: str

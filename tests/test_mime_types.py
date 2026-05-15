"""Unit tests for core.mime_types."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.mime_types import file_type_from_url
from models import FileType


@pytest.mark.parametrize("url,expected", [
    ("https://example.com/report.pdf", FileType.PDF),
    ("https://example.com/data.xlsx", FileType.SPREADSHEET),
    ("https://example.com/data.xls", FileType.SPREADSHEET),
    ("https://example.com/data.csv", FileType.SPREADSHEET),
    ("https://example.com/slide.pptx", FileType.PPT),
    ("https://example.com/slide.ppt", FileType.PPT),
    ("https://example.com/photo.jpg", FileType.IMAGE),
    ("https://example.com/photo.PNG", FileType.IMAGE),
    ("https://example.com/scan.tiff", FileType.IMAGE),
    ("https://example.com/file.xyz", FileType.UNKNOWN),
    ("https://example.com/noext", FileType.UNKNOWN),
])
def test_from_url(url, expected):
    assert file_type_from_url(url) == expected


@pytest.mark.parametrize("url,ct,expected", [
    ("https://example.com/x", "application/pdf", FileType.PDF),
    ("https://example.com/x", "application/vnd.ms-excel", FileType.SPREADSHEET),
    ("https://example.com/x", "image/jpeg", FileType.IMAGE),
    ("https://example.com/x", "application/vnd.openxmlformats-officedocument.presentationml.presentation", FileType.PPT),
    # Content-type takes precedence over URL extension when present
    ("https://example.com/data.pdf", "image/jpeg", FileType.IMAGE),
])
def test_from_content_type(url, ct, expected):
    assert file_type_from_url(url, content_type=ct) == expected


def test_query_string_stripped():
    assert file_type_from_url("https://example.com/report.pdf?token=abc") == FileType.PDF

"""Unit tests for core.file_router — no network, no LLM."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.file_router import categorise, flatten_tree
from models import FileType


class TestFlattenTree:
    def test_bare_string(self):
        items = flatten_tree("https://example.com/deck.pdf")
        assert len(items) == 1
        assert items[0].file_type == FileType.PDF

    def test_leaf_dict(self):
        items = flatten_tree({"url": "https://example.com/data.xlsx", "label": "Financials"})
        assert len(items) == 1
        assert items[0].label == "Financials"
        assert items[0].file_type == FileType.SPREADSHEET

    def test_list_of_urls(self):
        items = flatten_tree([
            "https://example.com/a.pdf",
            "https://example.com/b.pptx",
            "https://example.com/c.png",
        ])
        assert len(items) == 3
        types = {i.file_type for i in items}
        assert FileType.PDF in types
        assert FileType.PPT in types
        assert FileType.IMAGE in types

    def test_nested_dict(self):
        tree = {
            "data_room": {
                "financials": [
                    {"url": "https://example.com/pl.xlsx", "label": "P&L"},
                ],
                "deck": {"url": "https://example.com/pitch.pptx"},
            }
        }
        items = flatten_tree(tree)
        assert len(items) == 2
        assert any(i.file_type == FileType.SPREADSHEET for i in items)
        assert any(i.file_type == FileType.PPT for i in items)

    def test_children_key(self):
        tree = {"children": ["https://example.com/scan.png"]}
        items = flatten_tree(tree)
        assert len(items) == 1
        assert items[0].file_type == FileType.IMAGE

    def test_unknown_extension(self):
        items = flatten_tree("https://example.com/file.xyz")
        assert items[0].file_type == FileType.UNKNOWN

    def test_query_string_ignored(self):
        items = flatten_tree("https://example.com/report.pdf?token=abc&v=2")
        assert items[0].file_type == FileType.PDF

    def test_empty_tree(self):
        assert flatten_tree({}) == []
        assert flatten_tree([]) == []


class TestCategorise:
    def test_groups_by_type(self):
        items = flatten_tree([
            "https://example.com/a.pdf",
            "https://example.com/b.pdf",
            "https://example.com/c.xlsx",
        ])
        by_type = categorise(items)
        assert len(by_type[FileType.PDF]) == 2
        assert len(by_type[FileType.SPREADSHEET]) == 1
        assert len(by_type[FileType.IMAGE]) == 0

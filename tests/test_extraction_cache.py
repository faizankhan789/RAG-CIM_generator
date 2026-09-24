"""Saved per-document findings: an unchanged file isn't re-read by Claude on
regeneration — same findings in, same CIM input, lower cost."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pymysql
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from core import extraction_cache
from core.extraction_cache import _db_get as real_db_get   # before the autouse stub
from core.llm import extract_from_content

BLOCKS = [{"type": "text", "text": "FY2024 revenue: $12,900,000"}]


def _client(text="## Findings\n- revenue $12,900,000"):
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=MagicMock(
        content=[MagicMock(text=text)], usage=MagicMock(input_tokens=10, output_tokens=5)))
    return client


def test_key_changes_with_content_listing_prompt_or_model():
    base = extraction_cache.cache_key(BLOCKS, "<l/>", "prompt", "m1")
    assert base == extraction_cache.cache_key(list(BLOCKS), "<l/>", "prompt", "m1")
    assert base != extraction_cache.cache_key([{"type": "text", "text": "other"}], "<l/>", "prompt", "m1")
    assert base != extraction_cache.cache_key(BLOCKS, "<l2/>", "prompt", "m1")
    assert base != extraction_cache.cache_key(BLOCKS, "<l/>", "prompt v2", "m1")
    assert base != extraction_cache.cache_key(BLOCKS, "<l/>", "prompt", "m2")


@pytest.mark.asyncio
async def test_second_extraction_of_same_file_uses_saved_findings(findings_store):
    client = _client()
    with patch("core.llm.get_client", return_value=client):
        first = await extract_from_content(BLOCKS, "doc.pdf", "<l/>")
        second = await extract_from_content(BLOCKS, "doc.pdf", "<l/>")
    assert first == second == "## Findings\n- revenue $12,900,000"
    assert client.messages.create.await_count == 1          # second time: no Claude call
    assert len(findings_store) == 1


@pytest.mark.asyncio
async def test_changed_file_is_read_again():
    client = _client()
    with patch("core.llm.get_client", return_value=client):
        await extract_from_content(BLOCKS, "doc.pdf", "<l/>")
        await extract_from_content([{"type": "text", "text": "FY2025 revenue: $14,200,000"}], "doc.pdf", "<l/>")
    assert client.messages.create.await_count == 2


@pytest.mark.asyncio
async def test_failed_extraction_is_not_saved(findings_store):
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=RuntimeError("API down"))
    with patch("core.llm.get_client", return_value=client):
        assert await extract_from_content(BLOCKS, "doc.pdf", "<l/>") == ""
    assert findings_store == {}


@pytest.mark.asyncio
async def test_cache_db_errors_never_break_extraction():
    client = _client()
    with patch("core.extraction_cache._db_get", side_effect=RuntimeError("db down")), \
         patch("core.extraction_cache._db_put", side_effect=RuntimeError("db down")), \
         patch("core.llm.get_client", return_value=client):
        assert await extract_from_content(BLOCKS, "doc.pdf", "<l/>") == "## Findings\n- revenue $12,900,000"


def test_db_get_returns_none_when_table_does_not_exist_yet():
    def _raise_missing(fn):
        raise pymysql.err.ProgrammingError(1146, "Table 'extraction_cache' doesn't exist")
    with patch("core.extraction_cache._run_db", side_effect=_raise_missing):
        assert real_db_get("k") is None

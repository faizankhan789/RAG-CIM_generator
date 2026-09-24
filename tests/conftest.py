"""Shared test fixtures."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _no_real_facts_call():
    """formatter_node builds a verified facts table with a real Claude call
    (core/facts.py:build_facts). No test may spend real API credits, so it is
    stubbed to "no facts" everywhere; tests that need facts patch it themselves."""
    with patch("nodes.formatter.build_facts", new=AsyncMock(return_value=[])):
        yield


@pytest.fixture(autouse=True)
def findings_store():
    """Saved-findings cache (core/extraction_cache.py) backed by an in-memory dict
    in every test — no test ever touches the real shared DB."""
    store: dict[str, str] = {}
    with patch("core.extraction_cache._db_get", side_effect=lambda key: store.get(key)), \
         patch("core.extraction_cache._db_put", side_effect=lambda key, findings, model: store.__setitem__(key, findings)):
        yield store

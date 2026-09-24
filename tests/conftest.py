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

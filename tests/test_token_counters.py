"""Tests for core.llm's per-pipeline token accumulator.

Regression guard for the concurrency bug where the counter was a plain module
global: two overlapping CIM generations clobbered each other's totals, so the
input_tokens / output_tokens / estimated_price written to cim_generation_log
were cross-contaminated. It's a ContextVar now — one dict per pipeline task.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()  # core.llm reads CIM_MODEL at import time, same as server.py does

from core.llm import _add_tokens, get_token_counts, reset_token_counters


def test_get_returns_zero_without_a_pipeline_context():
    """Called outside any reset_token_counters() scope — must not raise."""
    assert get_token_counts() == (0, 0)


def test_add_is_a_noop_without_a_pipeline_context():
    _add_tokens(500, 20)  # no context established
    assert get_token_counts() == (0, 0)


def test_single_pipeline_accumulates():
    async def run():
        reset_token_counters()
        _add_tokens(1000, 50)
        _add_tokens(2000, 80)
        return get_token_counts()

    assert asyncio.run(run()) == (3000, 130)


def test_concurrent_pipelines_keep_separate_counts():
    """The actual bug: interleaved reset()/_add_tokens() from two pipelines.
    Child tasks (extraction calls) inherit their parent's context."""

    async def pipeline(adds: list[tuple[int, int]]) -> tuple[int, int]:
        reset_token_counters()
        for inp, out in adds:
            _add_tokens(inp, out)
            await asyncio.sleep(0.005)  # force interleaving with the other pipeline

        async def extraction_subtask() -> None:
            _add_tokens(100, 10)

        await asyncio.create_task(extraction_subtask())
        return get_token_counts()

    async def main():
        return await asyncio.gather(
            pipeline([(1000, 50), (2000, 80)]),
            pipeline([(7, 1), (3, 1)]),
        )

    a, b = asyncio.run(main())
    assert a == (3100, 140)   # own 3000/130 + subtask 100/10, no bleed from b
    assert b == (110, 12)     # own 10/2 + subtask 100/10, no bleed from a

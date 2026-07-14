"""Node timing wrapper + per-file timer for CIM pipeline profiling."""

from __future__ import annotations

import functools
import time
import logging

log = logging.getLogger("cim_timing")


def timed_node(fn):
    """Wrap a LangGraph node async fn to log wall-clock time."""
    @functools.wraps(fn)
    async def wrapper(state):
        t0 = time.monotonic()
        result = await fn(state)
        elapsed = time.monotonic() - t0
        log.debug("NODE  %-22s  %.2fs", fn.__name__, elapsed)
        return result
    return wrapper


class FileTimer:
    """Context manager for per-file timing inside processor nodes."""

    def __init__(self, node: str, label: str):
        self.node = node
        self.label = label[:60]
        self._t0 = 0.0

    def __enter__(self):
        self._t0 = time.monotonic()
        return self

    def __exit__(self, exc_type, *_):
        elapsed = time.monotonic() - self._t0
        status = "ERR " if exc_type else "OK  "
        log.debug("FILE  [%-12s] %s  %-60s  %.2fs", self.node, status, self.label, elapsed)

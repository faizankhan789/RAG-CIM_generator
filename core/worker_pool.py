import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Sentinel placed in the queue to signal a worker to exit cleanly.
# Using a unique object ensures it can never be confused with a real work item.
_STOP = object()


class WorkerPool:
    """
    A fixed-size async worker pool backed by a queue.
    Each instance has its own queue and worker tasks, so different
    task types (download, chunking, retrieval, …) get isolated
    concurrency budgets and never block each other.
    """

    def __init__(self, concurrency: int, name: str = "") -> None:
        self._concurrency = concurrency
        self._name = name
        # Initialised in start() once an event loop is running.
        self._queue: asyncio.Queue = None  # type: ignore[assignment]
        self._workers: list[asyncio.Task] = []

    async def start(self) -> None:
        """Create the queue and spin up worker tasks. Call once at app startup."""
        self._queue = asyncio.Queue()
        self._workers = [
            asyncio.create_task(self._worker(), name=f"{self._name}-worker-{i}")
            for i in range(self._concurrency)
        ]
        logger.info("Pool '%s' started with %d workers", self._name, self._concurrency)

    async def stop(self) -> None:
        """
        Gracefully shut down the pool. Each worker finishes its current task
        before exiting — no in-flight work is cancelled.
        """
        logger.info("Pool '%s' shutting down — draining %d workers", self._name, len(self._workers))
        # One sentinel per worker; each worker consumes exactly one before returning.
        for _ in self._workers:
            await self._queue.put(_STOP)
        await asyncio.gather(*self._workers)
        self._workers.clear()
        self._queue = None  # type: ignore[assignment]
        logger.info("Pool '%s' stopped", self._name)

    async def submit(self, coro: Coroutine[Any, Any, T]) -> T:
        """Enqueue a coroutine and wait for its result."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[T] = loop.create_future()
        await self._queue.put((coro, future))
        logger.debug("Pool '%s' — item enqueued (queue size: %d)", self._name, self._queue.qsize())
        return await future

    async def _worker(self) -> None:
        logger.debug("Worker '%s' ready", self._name)
        # Block waiting for the next item. This yields control to the event loop,
        # so idle workers consume no CPU.
        while True:
            item = await self._queue.get()

            if item is _STOP:
                # Sentinel received — acknowledge and exit.
                self._queue.task_done()
                logger.debug("Worker '%s' received stop signal, exiting", self._name)
                return

            coro, future = item
            try:
                result = await coro
                # Resolve the future so the submit() caller gets its result.
                future.set_result(result)
            except Exception as exc:
                # Propagate the exception to the submit() caller instead of silently dropping it.
                logger.error("Worker '%s' task raised an exception: %s", self._name, exc, exc_info=True)
                future.set_exception(exc)
            finally:
                # Always mark the item done so queue.join() can track completion.
                self._queue.task_done()

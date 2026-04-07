import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from core.logging import setup_logging
from core.pools import all_pools
from routers import cim
import services.cim as cim_service

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application starting up")

    # Start all worker pools before accepting requests.
    await asyncio.gather(*[pool.start() for pool in all_pools])

    # Single shared HTTP client for connection pooling across all download workers.
    # Assigned directly into the service module so workers can reach it without
    # passing it through every call.
    async with httpx.AsyncClient(timeout=60.0) as client:
        cim_service._http_client = client
        yield

    # Stop all worker pools after the server stops accepting requests.
    await asyncio.gather(*[pool.stop() for pool in all_pools])
    logger.info("Application shut down")


app = FastAPI(title="CIM API", lifespan=lifespan)

app.include_router(cim.router)

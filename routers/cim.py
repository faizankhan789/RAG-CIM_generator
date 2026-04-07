import logging

from fastapi import APIRouter, BackgroundTasks, Depends

from core.dependencies import verify_signature
from models import ListingRequest
from services.cim import download_files

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cim", tags=["CIM"])


@router.post("/generate", dependencies=[Depends(verify_signature)])
async def generate_cim(payload: ListingRequest, background_tasks: BackgroundTasks) -> dict:
    # Enqueue the download job and return immediately. The download runs in
    # the background via the global worker pool in services/cim.py.
    logger.info(
        "Received generate request for listing '%s' — %d item(s)",
        payload.listing_data.name, len(payload.listing_files),
    )
    background_tasks.add_task(download_files, payload.listing_files)
    return {"status": "ok"}

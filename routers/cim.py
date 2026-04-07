from fastapi import APIRouter, Depends

from core.dependencies import verify_signature
from models import ListingRequest
from services.cim import download_files

router = APIRouter(prefix="/cim", tags=["CIM"])


@router.post("/generate", dependencies=[Depends(verify_signature)])
async def generate_cim(payload: ListingRequest) -> dict:
    downloaded = download_files(payload.listing_files, destination="/tmp/cim")
    return {"status": "ok", "downloaded": downloaded}

from fastapi import HTTPException, Security
from fastapi.security.api_key import APIKeyHeader

from core.config import settings

api_key_scheme = APIKeyHeader(name=settings.signature_header_name, auto_error=False)


async def verify_signature(api_key: str = Security(api_key_scheme)) -> None:
    if api_key != settings.signature_header_value:
        raise HTTPException(status_code=401, detail="Invalid or missing signature header")

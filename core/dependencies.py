import logging

from fastapi import HTTPException, Security
from fastapi.security.api_key import APIKeyHeader

from core.config import settings

logger = logging.getLogger(__name__)

# Reads the signature value from the configured header on every request.
# auto_error=False lets us return a custom 401 instead of FastAPI's default 403.
api_key_scheme = APIKeyHeader(name=settings.signature_header_name, auto_error=False)


async def verify_signature(api_key: str = Security(api_key_scheme)) -> None:
    """Dependency that rejects requests whose signature header is absent or incorrect."""
    if api_key != settings.signature_header_value:
        logger.warning("Rejected request — invalid or missing signature header")
        raise HTTPException(status_code=401, detail="Invalid or missing signature header")

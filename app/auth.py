import secrets

from fastapi import Header, HTTPException, status

from app.config import get_settings


async def require_api_key(x_api_key: str | None = Header(default=None)) -> str:
    """Authenticate on a static API key.

    compare_digest rather than ==: string equality short-circuits on the first
    differing byte, which leaks key material through response timing.
    """
    settings = get_settings()
    if x_api_key is None or not secrets.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": "missing or invalid X-API-Key"},
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return x_api_key

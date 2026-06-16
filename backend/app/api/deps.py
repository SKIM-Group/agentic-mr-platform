"""FastAPI dependency injection."""

from fastapi import Header, HTTPException, status

from app.core.config import settings


async def verify_api_key(authorization: str | None = Header(default=None)) -> None:
    """Check Bearer token against APP_API_KEY. Skip if APP_API_KEY is 'dev-key'."""
    if settings.app_api_key == "dev-key":
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    token = authorization.removeprefix("Bearer ").strip()
    if token != settings.app_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

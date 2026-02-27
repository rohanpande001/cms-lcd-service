from fastapi import APIRouter

from app.cms_client.token_manager import token_manager
from app.core.config import get_settings
from app.schemas.coverage_schemas import TokenStatusResponse

router = APIRouter()


@router.get("/health", summary="Service health check")
async def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
    }


@router.get(
    "/v1/lcd/token-status",
    response_model=TokenStatusResponse,
    summary="CMS Bearer token status",
    description="Returns the current state of the cached CMS API Bearer token.",
)
async def token_status() -> TokenStatusResponse:
    return TokenStatusResponse(**token_manager.status)

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.core.config import get_settings
from app.core.exceptions import TokenRefreshError

logger = logging.getLogger(__name__)


class TokenManager:
    """
    Process-level singleton for CMS Bearer token lifecycle management.

    Uses asyncio.Lock to prevent concurrent refresh races (thundering herd).
    Refreshes lazily on demand with a 5-minute buffer before actual expiry,
    so tokens never expire mid-request.

    Fast path (valid token): no lock acquired.
    Slow path (expired/missing): acquires lock, double-checks, then fetches.
    """

    _instance: "TokenManager | None" = None

    def __new__(cls) -> "TokenManager":
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._token: str | None = None
            instance._expires_at: datetime | None = None
            instance._lock: asyncio.Lock | None = None
            cls._instance = instance
        return cls._instance

    def _get_lock(self) -> asyncio.Lock:
        # Lazily create lock inside the running event loop
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _is_valid(self) -> bool:
        if self._token is None or self._expires_at is None:
            return False
        settings = get_settings()
        buffer = timedelta(minutes=settings.TOKEN_REFRESH_BUFFER_MINUTES)
        return datetime.now(tz=timezone.utc) < (self._expires_at - buffer)

    async def get_token(self) -> str:
        """Return a valid Bearer token, refreshing if needed."""
        if self._is_valid():
            return self._token

        async with self._get_lock():
            if self._is_valid():  # Double-check after acquiring lock
                return self._token
            await self._fetch()
            return self._token

    async def _fetch(self) -> None:
        settings = get_settings()
        url = f"{settings.CMS_LCD_BASE_URL}/metadata/license-agreement"
        params = {
            "ama": str(settings.CMS_LICENSE_AMA).lower(),
            "ada": str(settings.CMS_LICENSE_ADA).lower(),
            "aha": str(settings.CMS_LICENSE_AHA).lower(),
        }
        logger.info("Fetching new CMS Bearer token...")
        try:
            async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

            token_list = data.get("data", [])
            if not token_list:
                raise TokenRefreshError(
                    "CMS license-agreement API returned empty data list",
                    details={"response": data},
                )

            self._token = token_list[0].get("Token") or token_list[0].get("token")
            if not self._token:
                raise TokenRefreshError(
                    "Token field missing in CMS response",
                    details={"first_item": token_list[0]},
                )

            self._expires_at = datetime.now(tz=timezone.utc) + timedelta(
                minutes=settings.TOKEN_EXPIRY_MINUTES
            )
            logger.info(
                "CMS token acquired. Expires at %s (buffer: %d min)",
                self._expires_at.isoformat(),
                settings.TOKEN_REFRESH_BUFFER_MINUTES,
            )

        except httpx.HTTPStatusError as e:
            raise TokenRefreshError(
                f"CMS token fetch failed: HTTP {e.response.status_code}",
                details={"body": e.response.text},
            )
        except httpx.RequestError as e:
            raise TokenRefreshError(f"CMS token fetch network error: {e}")

    def invalidate(self) -> None:
        """Force next call to refresh the token (useful for 401 handling)."""
        self._token = None
        self._expires_at = None
        logger.info("CMS token invalidated — will refresh on next request")

    @property
    def status(self) -> dict:
        now = datetime.now(tz=timezone.utc)
        remaining = (self._expires_at - now).total_seconds() / 60 if self._expires_at else None
        return {
            "has_token": self._token is not None,
            "expires_at": self._expires_at.isoformat() if self._expires_at else None,
            "minutes_remaining": round(remaining, 1) if remaining is not None else None,
            "is_valid": self._is_valid(),
        }


# Module-level singleton
token_manager = TokenManager()

import logging

import httpx

from app.cms_client.token_manager import token_manager
from app.core.config import get_settings
from app.core.exceptions import CMSAPIException, TokenRefreshError

logger = logging.getLogger(__name__)


class CMSApiClient:
    """
    Low-level CMS LCD API HTTP client.

    Uses a single persistent AsyncClient (connection pooling) managed via
    FastAPI lifespan (start/stop). All methods inject Bearer token automatically.

    If CMS returns 401, token is invalidated and the request is retried once.
    """

    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """Called on FastAPI startup to initialize the persistent HTTP client."""
        settings = get_settings()
        self._client = httpx.AsyncClient(
            base_url=settings.CMS_LCD_BASE_URL,
            timeout=settings.HTTP_TIMEOUT_SECONDS,
        )
        logger.info("CMS API HTTP client started (base_url=%s)", settings.CMS_LCD_BASE_URL)

    async def stop(self) -> None:
        """Called on FastAPI shutdown to cleanly close the connection pool."""
        if self._client:
            await self._client.aclose()
            logger.info("CMS API HTTP client closed")

    async def _auth_headers(self) -> dict:
        token = await token_manager.get_token()
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async def _get(self, path: str, params: dict, retry_on_401: bool = True) -> dict:
        headers = await self._auth_headers()
        try:
            response = await self._client.get(path, headers=headers, params=params)

            if response.status_code == 401 and retry_on_401:
                logger.warning("CMS API returned 401 — invalidating token and retrying")
                token_manager.invalidate()
                return await self._get(path, params, retry_on_401=False)

            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            raise CMSAPIException(
                f"CMS API error [{path}]: HTTP {e.response.status_code}",
                status_code=502,
                details={"path": path, "params": params, "cms_body": e.response.text[:500]},
            )
        except httpx.RequestError as e:
            raise CMSAPIException(
                f"CMS API network error [{path}]: {e}",
                status_code=503,
                details={"path": path},
            )

    # -------------------------------------------------------------------------
    # Public CMS endpoint wrappers
    # -------------------------------------------------------------------------

    async def find_articles_by_cpt(self, cpt_code: str) -> list[dict]:
        """
        Strategy A: Reverse lookup — find LCD articles governing a CPT code.

        Attempts GET /data/article/hcpc-code?hcpcCode={cpt_code} (no articleid).
        CMS may or may not support this filter without articleid — probe at runtime.

        Returns empty list if CMS doesn't support reverse lookup (caller handles fallback).
        """
        try:
            data = await self._get(
                "/data/article/hcpc-code",
                params={"hcpcCode": cpt_code},
            )
            return data.get("data", [])
        except CMSAPIException as e:
            logger.warning(
                "Reverse CPT lookup failed for %s (may require articleid): %s",
                cpt_code, e.message,
            )
            return []

    async def get_article_cpt_codes(self, article_id: str, page_size: int = None) -> list[dict]:
        """GET /data/article/hcpc-code?articleid={article_id}"""
        params = {"articleid": article_id}
        if page_size:
            params["page_size"] = page_size
        data = await self._get("/data/article/hcpc-code", params=params)
        return data.get("data", [])

    async def get_article_icd10_codes(self, article_id: str, page_size: int = None) -> list[dict]:
        """GET /data/article/icd10-covered?articleid={article_id}"""
        params = {"articleid": article_id}
        if page_size:
            params["page_size"] = page_size
        data = await self._get("/data/article/icd10-covered", params=params)
        return data.get("data", [])

    async def get_article_modifiers(self, article_id: str) -> list[dict]:
        """
        GET /data/article/modifier?articleid={article_id}

        NOTE: Modifier endpoint URL was not present in the Postman collection.
        Path '/data/article/modifier' is inferred from naming patterns.
        Returns empty list on failure so other data is still returned.
        """
        data = await self._get("/data/article/modifier", params={"articleid": article_id})
        return data.get("data", [])


# Module-level singleton — managed by FastAPI lifespan
cms_api_client = CMSApiClient()

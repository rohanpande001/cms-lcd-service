import asyncio
import logging

from app.cms_client.cms_api import cms_api_client
from app.core.exceptions import ArticleNotFoundError, CMSAPIException
from app.core.hcpc_lookup import find_article_ids
from app.schemas.coverage_schemas import (
    CptHcpcsCode,
    Icd10Code,
    LCDCoverageResponse,
    ModifierCode,
)

logger = logging.getLogger(__name__)


class CoverageService:
    """
    Orchestrates the full LCD coverage lookup for a given CPT code.

    Flow:
      1. Resolve article_id — from caller param (fast) or CMS reverse lookup
      2. Parallel-fetch CPT codes, ICD-10 codes, modifier codes via asyncio.gather
      3. Assemble and return unified LCDCoverageResponse

    Uses return_exceptions=True on gather so a single failing sub-call
    (e.g. unconfirmed modifier endpoint) doesn't discard all other data.
    """

    async def get_coverage_for_cpt(
        self,
        cpt_code: str,
        article_id: str | None = None,
    ) -> LCDCoverageResponse:
        resolved_id = article_id or await self._resolve_article_id(cpt_code)

        logger.info(
            "Fetching coverage data in parallel [cpt=%s, article=%s]",
            cpt_code, resolved_id,
        )

        results = await asyncio.gather(
            cms_api_client.get_article_cpt_codes(resolved_id),
            cms_api_client.get_article_icd10_codes(resolved_id),
            cms_api_client.get_article_modifiers(resolved_id),
            return_exceptions=True,
        )

        cpt_codes     = self._unwrap(results[0], "cpt_hcpcs_codes",  resolved_id)
        icd10_codes   = self._unwrap(results[1], "icd10_codes",      resolved_id)
        modifier_codes = self._unwrap(results[2], "modifier_codes",  resolved_id)

        return LCDCoverageResponse(
            cpt_code_queried=cpt_code,
            article_id=resolved_id,
            cpt_hcpcs_codes=[CptHcpcsCode.model_validate(c) for c in cpt_codes],
            icd10_covered_codes=[Icd10Code.model_validate(c) for c in icd10_codes],
            modifier_codes=[ModifierCode.model_validate(c) for c in modifier_codes],
            total_cpt_codes=len(cpt_codes),
            total_icd10_codes=len(icd10_codes),
            total_modifier_codes=len(modifier_codes),
        )

    async def _resolve_article_id(self, cpt_code: str) -> str:
        """
        Resolve the LCD article governing a CPT code.

        Strategy:
          1. Local lookup — check article_hcpc_mapping.csv (fast, no network call)
          2. CMS reverse lookup — fallback if not found locally
        Raises ArticleNotFoundError if neither source finds a match.
        """
        # 1. Local CSV lookup
        local_matches = find_article_ids(cpt_code)
        if local_matches:
            article_id = local_matches[0]
            if len(local_matches) > 1:
                logger.info(
                    "Local lookup found %d articles for CPT %s, using first: %s (all: %s)",
                    len(local_matches), cpt_code, article_id, local_matches,
                )
            else:
                logger.info("Local lookup resolved article_id=%s for CPT code %s", article_id, cpt_code)
            return article_id

        # 2. CMS reverse lookup fallback
        logger.info(
            "CPT %s not in local mapping — attempting CMS reverse lookup", cpt_code
        )
        articles = await cms_api_client.find_articles_by_cpt(cpt_code)

        if not articles:
            raise ArticleNotFoundError(cpt_code)

        first = articles[0]
        article_id = str(first.get("articleId") or first.get("article_id") or "")
        if not article_id:
            raise ArticleNotFoundError(cpt_code)

        logger.info("CMS reverse lookup resolved article_id=%s for CPT code %s", article_id, cpt_code)
        return article_id

    def _unwrap(self, result, field: str, article_id: str) -> list[dict]:
        """
        asyncio.gather with return_exceptions=True returns Exception instances
        instead of raising. Log and return empty list so partial data is usable.
        """
        if isinstance(result, Exception):
            logger.warning(
                "Failed to fetch %s for article %s: %s",
                field, article_id, result,
            )
            return []
        return result


# Module-level singleton
coverage_service = CoverageService()

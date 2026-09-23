import asyncio
import logging

from app.cms_client.cms_api import cms_api_client
from app.core.exceptions import ArticleNotFoundError, CMSAPIException
from app.core.audit import log_decision
from app.core.hcpc_lookup import (
    filter_by_jurisdiction,
    find_candidates,
    find_covering_article_ids,
    icd10_count,
)
from app.schemas.coverage_schemas import (
    CandidateArticle,
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
      1. Resolve article_id:
           a. caller-provided (fastest)
           b. local HCPC→article candidates, disambiguated by the patient's
              ICD-10 diagnosis when provided (CPT ∩ diagnosis)
           c. CMS reverse lookup fallback
      2. Parallel-fetch CPT codes, ICD-10 codes, modifier codes via asyncio.gather
      3. Assemble and return unified LCDCoverageResponse

    Uses return_exceptions=True on gather so a single failing sub-call
    (e.g. unconfirmed modifier endpoint) doesn't discard all other data.
    """

    async def get_coverage_for_cpt(
        self,
        cpt_code: str,
        article_id: str | None = None,
        icd10: str | None = None,
        state: str | None = None,
        contractor: str | None = None,
    ) -> LCDCoverageResponse:
        candidates = find_candidates(cpt_code)
        local, excluded, jurisdiction = filter_by_jurisdiction(
            candidates, state_query=state, contractor_query=contractor,
        )
        resolved_id, selection, coverage_source = await self._resolve_article_id(
            cpt_code,
            article_id=article_id,
            icd10=icd10,
            candidates=local,
            jurisdiction_active=bool(state or contractor),
        )

        covering = find_covering_article_ids(icd10) if icd10 else None

        logger.info(
            "Fetching coverage data in parallel [cpt=%s, article=%s, icd10=%s, selection=%s, jurisdiction=%s]",
            cpt_code, resolved_id, icd10 or "-", selection, jurisdiction,
        )

        results = await asyncio.gather(
            cms_api_client.get_article_cpt_codes(resolved_id),
            cms_api_client.get_article_icd10_codes(resolved_id),
            cms_api_client.get_article_modifiers(resolved_id),
            return_exceptions=True,
        )

        cpt_codes      = self._unwrap(results[0], "cpt_hcpcs_codes",  resolved_id)
        icd10_codes    = self._unwrap(results[1], "icd10_codes",      resolved_id)
        modifier_codes = self._unwrap(results[2], "modifier_codes",   resolved_id)

        response = LCDCoverageResponse(
            cpt_code_queried=cpt_code,
            article_id=resolved_id,
            cpt_hcpcs_codes=[CptHcpcsCode.model_validate(c) for c in cpt_codes],
            icd10_covered_codes=[Icd10Code.model_validate(c) for c in icd10_codes],
            modifier_codes=[ModifierCode.model_validate(c) for c in modifier_codes],
            total_cpt_codes=len(cpt_codes),
            total_icd10_codes=len(icd10_codes),
            total_modifier_codes=len(modifier_codes),
            icd10_queried=icd10,
            article_selection=selection,
            coverage_source=coverage_source,
            jurisdiction=jurisdiction,
            excluded_articles=[c["article_id"] for c in excluded],
            candidate_articles=self._candidate_articles(
                candidates, covering, in_scope={c["article_id"] for c in local},
            ),
        )
        log_decision(
            endpoint="/v1/lcd/coverage",
            cpt_code=cpt_code,
            state=state,
            contractor=contractor,
            icd10=icd10,
            article_id=resolved_id,
            selection=selection,
            jurisdiction=jurisdiction,
            coverage_source=coverage_source,
        )
        return response

    async def _resolve_article_id(
        self,
        cpt_code: str,
        article_id: str | None,
        icd10: str | None,
        candidates: list[dict],
        jurisdiction_active: bool = False,
    ) -> tuple[str, str, str | None]:
        """
        Resolve the LCD article governing a CPT code.

        Returns (article_id, selection_reason, coverage_source).

        Strategy (in priority order):
          1. Caller-provided article_id — used as-is
          2. Local candidates + icd10 — CPT ∩ diagnosis; unique match wins,
             otherwise the most specific article (fewest ICD-10 codes) is used
          3. Jurisdiction scope active: the applicable local article (the one
             that governs the billing state/contractor) — never the national
             governing policy, which is what caused the Octagam denial
          4. A direct coverage policy article (type 1/4) among the candidates
          4. The governing policy article resolved from the "Billing and Coding"
             companion candidates (ICD-10 fingerprint; majority vote)
          5. First local candidate (legacy behavior, flagged)
          6. CMS reverse lookup fallback
        Raises ArticleNotFoundError if nothing resolves.
        """
        if article_id:
            ncd = self._ncd_for(article_id, candidates)
            return article_id, "caller-provided", ncd

        if not candidates:
            aid, selection = await self._cms_reverse_lookup(cpt_code)
            return aid, selection, None

        ncd = self._ncd_for(candidates[0]["article_id"], candidates) if candidates else None

        if icd10:
            covering = find_covering_article_ids(icd10)
            exact = [c for c in candidates if c["article_id"] in covering]
            if len(exact) == 1:
                logger.info(
                    "ICD-10 %s resolved uniquely to article %s for CPT %s",
                    icd10, exact[0]["article_id"], cpt_code,
                )
                return exact[0]["article_id"], "icd10-exact-match", self._ncd_for(exact[0]["article_id"], candidates)
            if len(exact) > 1:
                best = min(exact, key=lambda c: icd10_count(c["article_id"]))
                logger.info(
                    "ICD-10 %s matches %d articles for CPT %s; using most specific: %s",
                    icd10, len(exact), cpt_code, best["article_id"],
                )
                return (
                    best["article_id"],
                    f"icd10-match ({len(exact)} articles cover this diagnosis; "
                    f"most specific used — verify via candidate_articles)",
                    self._ncd_for(best["article_id"], candidates),
                )
            logger.warning(
                "ICD-10 %s is not in any article covering CPT %s — coverage may be "
                "governed by an NCD not present in the MCD database. Using first candidate.",
                icd10, cpt_code,
            )
            return (
                candidates[0]["article_id"],
                "no-candidate-covers-icd10 (first local candidate used — "
                "coverage may be NCD-governed; verify before filing)",
                ncd,
            )

        if jurisdiction_active:
            # A jurisdiction was provided: resolve to the article that actually
            # governs the billing state/contractor — never the national
            # governing policy (the failure mode behind the Octagam denial).
            if len(candidates) == 1:
                logger.info(
                    "CPT %s: jurisdiction scope leaves exactly one applicable article %s",
                    cpt_code, candidates[0]["article_id"],
                )
                return candidates[0]["article_id"], "jurisdiction-local-article", ncd
            logger.info(
                "CPT %s: %d articles apply in this jurisdiction; using first %s "
                "(all listed in candidate_articles — pass contractor or article_id to disambiguate)",
                cpt_code, len(candidates), candidates[0]["article_id"],
            )
            return (
                candidates[0]["article_id"],
                f"jurisdiction-local-article ({len(candidates)} articles apply in this "
                f"jurisdiction — pass contractor or article_id to disambiguate)",
                ncd,
            )

        # Direct coverage policy articles (type 1/4) beat companion articles (type 5/6)
        policy_candidates = [c for c in candidates if c["article_type"] in ("1", "4")]
        if policy_candidates:
            best = max(policy_candidates, key=lambda c: c["eff_date"])
            logger.info(
                "CPT %s has %d direct coverage policy article(s); using %s",
                cpt_code, len(policy_candidates), best["article_id"],
            )
            return (
                best["article_id"],
                "coverage-policy-article (directly mapped to this code)",
                self._ncd_for(best["article_id"], candidates),
            )

        # Companion articles resolve to a governing policy article via fingerprint
        governing = self._governing_article(candidates)
        if governing:
            gov_aid, support = governing
            logger.info(
                "CPT %s: %d of %d companion candidates resolve to governing policy %s",
                cpt_code, support, len(candidates), gov_aid,
            )
            return (
                gov_aid,
                f"governing-policy-article ({support} of {len(candidates)} companion "
                f"articles resolve to it — see candidate_articles)",
                self._ncd_for(gov_aid, candidates),
            )

        if len(candidates) == 1:
            return candidates[0]["article_id"], "first-local-match", ncd

        logger.info(
            "CPT %s maps to %d articles; no governing policy resolved, using first: %s (all: %s)",
            cpt_code, len(candidates), candidates[0]["article_id"],
            [c["article_id"] for c in candidates],
        )
        return (
            candidates[0]["article_id"],
            f"first-local-match ({len(candidates)} articles map to this code — "
            f"pass icd10 or article_id to disambiguate)",
            ncd,
        )

    @staticmethod
    def _governing_article(candidates: list[dict]) -> tuple[str, int] | None:
        """
        Majority-vote the governing policy article across companion candidates.

        Ties break by NCD agreement, then by first candidate order.
        """
        votes: dict[str, int] = {}
        for c in candidates:
            gov = c.get("governing_article_id", "")
            if gov:
                votes[gov] = votes.get(gov, 0) + 1
        if not votes:
            return None
        best_aid, best_count = max(votes.items(), key=lambda kv: (kv[1], -list(votes).index(kv[0])))
        return best_aid, best_count

    @staticmethod
    def _ncd_for(article_id: str, candidates: list[dict]) -> str | None:
        """Build a human-readable coverage source string (NCD citation) if known."""
        source = None
        for c in candidates:
            if c["article_id"] == article_id and c.get("ncd_id"):
                source = c
                break
        if source is None:
            counts: dict[str, int] = {}
            by_ncd: dict[str, dict] = {}
            for c in candidates:
                if c.get("ncd_id"):
                    counts[c["ncd_id"]] = counts.get(c["ncd_id"], 0) + 1
                    by_ncd.setdefault(c["ncd_id"], c)
            if counts:
                best = max(counts.items(), key=lambda kv: kv[1])[0]
                source = by_ncd[best]
        if source is None or not source.get("ncd_id"):
            return None
        section = f" NCD Manual §{source['ncd_section']} — " if source.get("ncd_section") else " "
        return f"NCD {source['ncd_id']},{section}{source.get('ncd_title', '')}"

    async def _cms_reverse_lookup(self, cpt_code: str) -> tuple[str, str]:
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

        logger.info("CMS reverse lookup resolved article_id=%s for CPT %s", article_id, cpt_code)
        return article_id, "cms-reverse-lookup"

    def _candidate_articles(
        self,
        candidates: list[dict],
        covering: set[str] | None,
        in_scope: set[str] | None = None,
    ) -> list[CandidateArticle]:
        if len(candidates) <= 1 and covering is None and in_scope is None:
            return []
        return [
            CandidateArticle(
                article_id=c["article_id"],
                display_id=c["display_id"] or None,
                title=c["article_title"] or None,
                article_type=c["article_type"] or None,
                status=c["status"] or None,
                eff_date=c["eff_date"] or None,
                end_date=c["end_date"] or None,
                ncd_id=c.get("ncd_id") or None,
                governing_article_id=c.get("governing_article_id") or None,
                icd10_covered=(c["article_id"] in covering) if covering is not None else None,
                in_jurisdiction=(c["article_id"] in in_scope) if in_scope is not None else None,
            )
            for c in candidates
        ]

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

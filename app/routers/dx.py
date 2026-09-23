from fastapi import APIRouter, HTTPException, Query

from app.core.audit import log_decision
from app.core.hcpc_lookup import (
    article_applies_in_state,
    article_dx_codes,
    article_states,
    check_dx,
    filter_by_jurisdiction,
    find_candidates,
    find_dx_codes,
    get_article,
    list_states,
    normalize_article_id,
)
from app.schemas.coverage_schemas import (
    ArticleResponse,
    CandidateArticle,
    DXCheckResponse,
    DXCodesResponse,
    DXCode,
    DXCodeSource,
    ErrorResponse,
)

router = APIRouter()


def _candidate_model(
    c: dict,
    icd10_covered: bool | None = None,
    in_jurisdiction: bool | None = None,
    role: str | None = None,
) -> CandidateArticle:
    return CandidateArticle(
        article_id=c["article_id"],
        display_id=c.get("display_id") or None,
        title=c.get("article_title") or None,
        article_type=c.get("article_type") or None,
        status=c.get("status") or None,
        eff_date=c.get("eff_date") or None,
        end_date=c.get("end_date") or None,
        ncd_id=c.get("ncd_id") or None,
        governing_article_id=c.get("governing_article_id") or None,
        icd10_covered=icd10_covered,
        in_jurisdiction=in_jurisdiction,
        role=role,
        cms_url=f"https://www.cms.gov/medicare-coverage-database/view/article.aspx?articleid={c['article_id']}",
    )


@router.get(
    "/article",
    response_model=ArticleResponse,
    responses={404: {"model": ErrorResponse, "description": "Article not found in the CMS crosswalk"}},
    summary="Search one LCD article by article number (PA workflow)",
    description="""
Search an article directly by its number (e.g. `59105` or `A59105`) — the way payers
reference policies in denials. Returns everything the local index knows about it:

- title, type, status, effective/end dates
- the official NCD link when CMS ties it to one
- governing policy article, MAC/contractors, and the states they serve
- every J/HCPCS/CPT code mapped to it
- **every ICD-10 code the article lists as covered** (with group attribution)

Fully local — no CMS API calls.

Example: `GET /v1/lcd/article?article_id=59105`
""",
)
async def get_article_endpoint(
    article_id: str = Query(..., description="LCD article number, with or without the A prefix", example="59105"),
    state: str | None = Query(None, description="Optional: check whether the article applies in this billing state", example="NY"),
) -> ArticleResponse:
    meta = get_article(article_id)
    if meta is None:
        raise HTTPException(
            status_code=404,
            detail=f"Article {article_id} not found in the current CMS crosswalk.",
        )

    dx_rows = article_dx_codes(meta["article_id"])
    log_decision(
        endpoint="/v1/lcd/article",
        cpt_code=meta["hcpc_codes"][0] if meta["hcpc_codes"] else "",
        article_id=meta["article_id"],
        selection="article-search",
        jurisdiction="article mode",
        coverage_source=(
            f"NCD {meta['ncd_id']}" if meta.get("ncd_id") else None
        ),
    )
    state_in_scope = article_applies_in_state(meta["article_id"], state) if state else None
    return ArticleResponse(
        article_id=meta["article_id"],
        display_id=meta.get("display_id") or None,
        title=meta.get("article_title") or None,
        article_type=meta.get("article_type") or None,
        status=meta.get("status") or None,
        eff_date=meta.get("eff_date") or None,
        end_date=meta.get("end_date") or None,
        ncd_id=meta.get("ncd_id") or None,
        ncd_section=meta.get("ncd_section") or None,
        ncd_title=meta.get("ncd_title") or None,
        governing_article_id=meta.get("governing_article_id") or None,
        contractor_names=meta.get("contractor_names", []),
        states=article_states(meta["article_id"]),
        state_in_scope=state_in_scope,
        hcpc_codes=meta["hcpc_codes"],
        total_dx_codes=len(dx_rows),
        dx_codes=[
            DXCode(
                code=r["code"],
                description=r["description"] or None,
                sources=[DXCodeSource(article_id=meta["article_id"], group=r["group"] or None)],
            )
            for r in dx_rows
        ],
    )


@router.get(
    "/states",
    summary="Jurisdictions available for state-scoped lookups",
    description="List of resolvable states (with abbreviations) for the `state` query parameter.",
)
async def get_states() -> list[dict]:
    return list_states()


@router.get(
    "/dx-codes",
    response_model=DXCodesResponse,
    responses={404: {"model": ErrorResponse, "description": "No coverage articles found for the J code"}},
    summary="All ICD-10 diagnosis codes supported for a J code (PA workflow)",
    description="""
Returns every ICD-10 diagnosis code that any CMS coverage document tied to the J code
lists as supporting medical necessity, with per-article source attribution.

This is the direct **J code → DX codes** lookup for Prior Auth filing.
Entirely local (no CMS API calls) — instant and works even when the CMS API is down.

**Jurisdiction scoping (recommended for PA):** many J codes map to one article per
MAC/contractor, and the covered diagnosis lists differ between them. Pass the
patient's `state` (e.g. `NY`) or `contractor` (e.g. `Palmetto`) to scope the result
to the articles that actually apply in the billing jurisdiction:

    GET /v1/lcd/dx-codes?cpt_code=J1568&state=NY

Without `state`/`contractor` the union spans every national article, which can
include diagnoses your payer's article does not accept (a common denial cause).
""",
)
async def get_dx_codes(
    cpt_code: str = Query(..., description="CPT/HCPCS procedure code", example="J1568"),
    state: str | None = Query(None, description="Billing state (abbr or name), e.g. NY", example="NY"),
    contractor: str | None = Query(None, description="MAC/contractor name (substring), e.g. Palmetto", example="Palmetto"),
) -> DXCodesResponse:
    candidates = find_candidates(cpt_code)
    if not candidates:
        raise HTTPException(
            status_code=404,
            detail=f"No coverage articles found for {cpt_code}. "
                   "The code may not be in the current CMS crosswalk.",
        )

    local, excluded, jurisdiction = filter_by_jurisdiction(
        candidates, state_query=state, contractor_query=contractor,
    )
    # PA scope: the "Billing and Coding" companion articles (CMS type 6) are the
    # documents whose ICD-10 lists the payer actually applies. National policy /
    # NCD articles (type 1/4) are reference only and stay out of the dx union.
    scope_candidates = [c for c in local if c.get("article_type") == "6"]
    scope_note = None
    if not scope_candidates:
        scope_candidates = local
        scope_note = (
            "No Billing & Coding (type 6) articles for this code in this scope — "
            "using the policy articles instead"
        )

    dx_codes = find_dx_codes(cpt_code, article_ids=[c["article_id"] for c in scope_candidates])
    local_ids = {c["article_id"] for c in local}

    return DXCodesResponse(
        cpt_code=cpt_code.upper(),
        total_dx_codes=len(dx_codes),
        dx_codes=[DXCode(**c) for c in dx_codes],
        candidate_articles=[
            _candidate_model(c, in_jurisdiction=c["article_id"] in local_ids)
            for c in candidates
        ],
        jurisdiction=jurisdiction,
        qualified_articles=[
            _candidate_model(c, in_jurisdiction=True, role="local") for c in scope_candidates
        ],
        scope_note=scope_note,
    )


@router.get(
    "/dx",
    response_model=DXCheckResponse,
    responses={404: {"model": ErrorResponse, "description": "No coverage articles found for the J code"}},
    summary="Verify a specific ICD-10 diagnosis against a J code",
    description="""
Answers the core PA question: **"is this patient's diagnosis supported by CMS coverage
for this drug?"**

- `supported: true` → at least one in-scope article covers the dx;
  `supporting_articles` is the citation to use on the PA
- `supported: false` + `code_known: true` → the dx exists but no in-scope article
  covers it (off-label or NCD-governed — verify against the payer's policy)
- **jurisdiction mismatch** → the dx is covered nationally but NOT by the article that
  applies in the billing `state`/`contractor`: the verdict says so explicitly
  (this is the failure mode that denies claims filed with a nationwide diagnosis list)
- `noncovered_in` → the dx is explicitly listed as NOT covered by some in-scope
  articles: a strong pre-filing denial signal

Pass `state` (e.g. `NY`) or `contractor` (e.g. `Palmetto`) to evaluate against the
jurisdiction that actually governs the claim.

Prefix-aware: claim code `D84.90` matches a listed `D84.9`.

Example: `GET /v1/lcd/dx?cpt_code=J1568&icd10=D69.3&state=NY`
""",
)
async def check_dx_endpoint(
    cpt_code: str | None = Query(None, description="CPT/HCPCS procedure code (omit in article mode)", example="J1568"),
    icd10: str = Query(..., description="Patient's ICD-10 diagnosis code", example="D69.3"),
    state: str | None = Query(None, description="Billing state (abbr or name), e.g. NY", example="NY"),
    contractor: str | None = Query(None, description="MAC/contractor name (substring), e.g. Palmetto", example="Palmetto"),
    article_id: str | None = Query(None, description="Article mode: verify the dx against this specific article number instead of a J code", example="59105"),
) -> DXCheckResponse:
    # Article mode: check the dx against one specific article number
    if article_id:
        aid = normalize_article_id(article_id)
        meta = get_article(aid)
        if meta is None:
            raise HTTPException(
                status_code=404,
                detail=f"Article {article_id} not found in the current CMS crosswalk.",
            )
        scope_ids = [aid]
        jurisdiction = f"article {aid}"
        result = check_dx("", icd10, article_ids=scope_ids)
        supporting = [_candidate_model(meta, icd10_covered=True)] if result["supported"] else []
        if not result["code_known"]:
            verdict = "ICD-10 code not found in the CMS index — verify the code is valid"
        elif result["supported"]:
            groups = sorted(
                {s["group"] for s in result["supporting"] if s.get("group")},
                key=lambda g: (int(g) if str(g).isdigit() else 0),
            )
            verdict = (
                f"SUPPORTED — article {aid} covers the diagnosis"
                + (f" (group {', '.join(groups)})" if groups else "")
                + " — safe to cite when the payer applies this article"
            )
        elif result["noncovered_in"]:
            verdict = (
                f"NOT COVERED — article {aid} explicitly lists the diagnosis as NOT covered. "
                f"Filing it will be denied — pick a dx from the article's covered list"
            )
        else:
            verdict = (
                f"NOT COVERED — article {aid}'s covered list does not include the diagnosis. "
                f"Pick a dx from the article's list (see GET /v1/lcd/article?article_id={aid})"
            )
        response = DXCheckResponse(
            cpt_code=None,
            icd10_queried=icd10.strip().upper(),
            supported=result["supported"],
            code_known=result["code_known"],
            supporting_articles=supporting,
            noncovered_in=result["noncovered_in"],
            jurisdiction=jurisdiction,
            article_mode=True,
            verdict=verdict,
        )
        log_decision(
            endpoint="/v1/lcd/dx",
            cpt_code="",
            icd10=icd10,
            article_id=aid,
            selection="article-mode",
            jurisdiction=jurisdiction,
            supported=result["supported"],
            verdict=verdict,
        )
        return response

    if not cpt_code:
        raise HTTPException(
            status_code=422,
            detail="Provide cpt_code (J-code mode) or article_id (article mode).",
        )

    candidates = find_candidates(cpt_code)
    if not candidates:
        raise HTTPException(
            status_code=404,
            detail=f"No coverage articles found for {cpt_code}. "
                   "The code may not be in the current CMS crosswalk.",
        )

    local, excluded, jurisdiction = filter_by_jurisdiction(
        candidates, state_query=state, contractor_query=contractor,
    )
    # Same PA scoping as /dx-codes: type 6 billing & coding articles only
    scope_candidates = [c for c in local if c.get("article_type") == "6"] or local
    scope_ids = [c["article_id"] for c in scope_candidates]
    scope_note = "" if [c for c in local if c.get("article_type") == "6"] else (
        " [no type 6 articles in scope — policy articles used] "
    )

    result = check_dx(cpt_code, icd10, article_ids=scope_ids)
    supporting_ids = {s["article_id"] for s in result["supporting"]}
    supporting = [
        _candidate_model(c, icd10_covered=(c["article_id"] in supporting_ids))
        for c in candidates
        if c["article_id"] in supporting_ids
    ]

    # Jurisdiction nuance: dx covered by another MAC's billing article but not
    # by the one that applies in this jurisdiction
    national_result = result
    if not result["supported"] and (state or contractor):
        national_candidates = [c for c in candidates if c.get("article_type") == "6"] or candidates
        national_result = check_dx(
            cpt_code, icd10, article_ids=[c["article_id"] for c in national_candidates]
        )

    if not result["code_known"]:
        verdict = "ICD-10 code not found in the CMS index — verify the code is valid"
    elif result["supported"] and not result["noncovered_in"]:
        verdict = (
            f"SUPPORTED — covered by {len(supporting)} billing & coding article(s) "
            f"[{jurisdiction}]{scope_note}; "
            f"cite {[s['article_id'] for s in result['supporting']]} on the PA"
        )
    elif result["supported"] and result["noncovered_in"]:
        verdict = (
            f"CAUTION — covered by {[s['article_id'] for s in result['supporting']]} but "
            f"explicitly NOT covered in {result['noncovered_in']} — verify which policy "
            f"the payer applies before filing"
        )
    elif not result["supported"] and national_result["supported"]:
        verdict = (
            f"NOT SUPPORTED IN THIS JURISDICTION [{jurisdiction}] — the diagnosis is "
            f"covered by another MAC's billing article(s) "
            f"({sorted(s['article_id'] for s in national_result['supporting'])}) "
            f"but NOT by the applicable article(s) {scope_ids}. Filing this dx against the "
            f"local policy will likely be denied — use a dx from the local article's list"
        )
    else:
        verdict = (
            "NOT SUPPORTED — no billing & coding article tied to this J code covers the "
            "diagnosis. Coverage may be NCD-governed or off-label; verify against the "
            "payer's policy"
        )

    response = DXCheckResponse(
        cpt_code=cpt_code.strip().upper(),
        icd10_queried=icd10.strip().upper(),
        supported=result["supported"],
        code_known=result["code_known"],
        supporting_articles=supporting,
        noncovered_in=result["noncovered_in"],
        jurisdiction=jurisdiction,
        verdict=verdict,
    )
    log_decision(
        endpoint="/v1/lcd/dx",
        cpt_code=cpt_code,
        state=state,
        contractor=contractor,
        icd10=icd10,
        article_id=",".join(scope_ids) if scope_ids else None,
        selection="jurisdiction-scoped" if (state or contractor) else "national",
        jurisdiction=jurisdiction,
        supported=result["supported"],
        verdict=verdict,
    )
    return response

from fastapi import APIRouter, HTTPException, Query

from app.core.exceptions import ArticleNotFoundError, CMSAPIException
from app.schemas.coverage_schemas import ErrorResponse, LCDCoverageResponse
from app.services.coverage_service import coverage_service

router = APIRouter()


@router.get(
    "/coverage",
    response_model=LCDCoverageResponse,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "No LCD article found for the given CPT code. "
                           "Provide 'article_id' directly to bypass reverse lookup.",
        },
        502: {"model": ErrorResponse, "description": "CMS upstream API error"},
        503: {"model": ErrorResponse, "description": "CMS API unreachable or token refresh failed"},
    },
    summary="Unified LCD coverage lookup for a CPT code",
    description="""
Given a CPT/HCPCS procedure code, this endpoint:

1. **Resolves** the governing LCD article (via CMS reverse lookup, or use `article_id` to skip)
2. **Fetches in parallel**: CPT/HCPCS codes, ICD-10 medical necessity codes, modifier codes
3. **Returns** a single unified JSON response

### Article disambiguation (Prior Auth)
Many CPT/HCPCS codes map to multiple LCD articles. To get the exact article that governs
a claim, pass the patient's diagnosis:

    GET /v1/lcd/coverage?cpt_code=J1568&icd10=D69.3

The service intersects the code's candidate articles with articles whose covered ICD-10
list contains the diagnosis (prefix-aware, so D84.90 matches D84.9). When several articles
match, the most specific one is used and all matches are listed in `candidate_articles`.

- **Jurisdiction**: pass the patient's `state` (e.g. `NY`) or `contractor` (e.g.
  `Palmetto`) — articles that do not apply in that jurisdiction are moved out of
  resolution and listed in `excluded_articles`. Without it, resolution is national
  and may pick a contractor-specific article that does not govern your claim.
- Use `article_id` if you already know the article — it skips resolution entirely.
- If no article covers the diagnosis, `article_selection` flags it — coverage may be
  governed by an NCD (e.g. NCD 250.3 for IVIG) that is not part of the MCD article database.

Useful in **Prior Authorization workflows** to validate whether a patient's diagnosis (ICD-10)
supports medical necessity for the ordered procedure (CPT code).
    """,
)
async def get_lcd_coverage(
    cpt_code: str = Query(
        ...,
        description="CPT/HCPCS procedure code to look up",
        example="82306",
    ),
    article_id: str | None = Query(
        None,
        description="Optional: LCD article ID. Skips CMS reverse lookup if provided.",
        example="52399",
    ),
    icd10: str | None = Query(
        None,
        description="Optional: patient's ICD-10 diagnosis. Narrows multi-article codes to the exact governing article (CPT ∩ diagnosis).",
        example="D69.3",
    ),
    state: str | None = Query(
        None,
        description="Optional: billing state (abbr or name, e.g. NY). Restricts resolution to articles that apply in that jurisdiction.",
        example="NY",
    ),
    contractor: str | None = Query(
        None,
        description="Optional: MAC/contractor name (substring, e.g. Palmetto). Restricts resolution to that contractor's articles.",
        example="Palmetto",
    ),
) -> LCDCoverageResponse:
    try:
        return await coverage_service.get_coverage_for_cpt(
            cpt_code=cpt_code,
            article_id=article_id,
            icd10=icd10,
            state=state,
            contractor=contractor,
        )
    except ArticleNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except CMSAPIException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)

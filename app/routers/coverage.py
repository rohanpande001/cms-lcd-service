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

Use the `article_id` parameter if you already know the article — it skips the reverse lookup
and reduces latency by one network call.

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
) -> LCDCoverageResponse:
    try:
        return await coverage_service.get_coverage_for_cpt(
            cpt_code=cpt_code,
            article_id=article_id,
        )
    except ArticleNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except CMSAPIException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)

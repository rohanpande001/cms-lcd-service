import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.cms_client.cms_api import cms_api_client
from app.core.config import get_settings
from app.core.exceptions import CMSAPIException
from app.routers import coverage, health

settings = get_settings()

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    await cms_api_client.start()
    logger.info("Ready")
    yield
    # Shutdown
    logger.info("Shutting down — closing HTTP client")
    await cms_api_client.stop()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    description="""
## CMS LCD Coverage API

A unified wrapper around the CMS Local Coverage Determination (LCD) API.

Given a CPT/HCPCS code, returns all coverage data in a **single call**:
- CPT/HCPCS codes governed by the LCD article
- ICD-10 codes establishing medical necessity
- Modifier codes

### Authentication
This service manages the CMS Bearer token automatically (auto-refresh before expiry).
No auth required on this API.

### Prior Authorization Use Case
Use `GET /v1/lcd/coverage?cpt_code=XXXXX` to check whether a patient's diagnosis
supports medical necessity for a procedure — a core step in PA workflows.
    """,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.exception_handler(CMSAPIException)
async def cms_exception_handler(request: Request, exc: CMSAPIException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.message, "detail": exc.details or None},
    )


# Routers
app.include_router(health.router, tags=["Health"])
app.include_router(coverage.router, prefix="/v1/lcd", tags=["LCD Coverage"])

# CMS LCD Coverage Service

A FastAPI microservice that wraps the CMS Local Coverage Determination (LCD) API and returns all coverage data for a CPT/HCPCS code in a **single call** — CPT codes, ICD-10 medical necessity codes, and modifier codes.

Built for **Prior Authorization workflows**: given a procedure code, instantly retrieve every ICD-10 diagnosis that establishes medical necessity.

---

## How It Works

```
Client
  │
  ▼
GET /v1/lcd/coverage?cpt_code=J9217
  │
  ├─► 1. Local HCPC→article lookup (article_hcpc_mapping.csv)
  │       4,184 codes, 1,115 articles — no network call
  │
  ├─► 2. CMS reverse lookup fallback (if not in local mapping)
  │       GET https://api.coverage.cms.gov/v1/data/article/hcpc-code
  │
  └─► 3. Parallel fetch from CMS API
          ├── CPT/HCPCS codes   GET /data/article/hcpc-code
          ├── ICD-10 codes      GET /data/article/icd10-covered
          └── Modifier codes    GET /data/article/modifier
              │
              ▼
          Unified JSON response
```

**Token management** is fully automatic — the service fetches and caches the CMS Bearer token, refreshing it 5 minutes before expiry so tokens never expire mid-request.

---

## Quick Start

### Requirements

- Python **3.11** (3.14 is incompatible with pinned `pydantic-core`)
- pyenv recommended: `pyenv install 3.11.8`

### Setup

```bash
# Clone the repo
git clone https://github.com/<your-username>/cms-lcd-service.git
cd cms-lcd-service

# Create virtualenv with Python 3.11
python3.11 -m venv venv311
source venv311/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env if needed (defaults work out of the box)
```

### Run

```bash
# Development (auto-reload on code changes)
make run

# Production (4 workers)
make run-prod
```

Server starts at **http://localhost:8000**

| URL | Description |
|---|---|
| http://localhost:8000/docs | Interactive Swagger UI |
| http://localhost:8000/redoc | ReDoc documentation |
| http://localhost:8000/health | Health check |

---

## API Reference

### `GET /v1/lcd/coverage`

Returns unified LCD coverage data for a CPT/HCPCS code.

**Parameters**

| Parameter | Required | Description |
|---|---|---|
| `cpt_code` | Yes | CPT/HCPCS procedure code (e.g. `J9217`) |
| `article_id` | No | Numeric LCD article ID. Skips lookup if provided. |

> **Note:** Pass the numeric article ID only (e.g. `52453`, not `A52453`). The CMS API rejects the `A` prefix.

**Article ID resolution (automatic)**

When `article_id` is omitted:
1. Checks `article_hcpc_mapping.csv` locally — instant, no network call
2. Falls back to CMS reverse lookup if not found locally
3. Returns `404` if neither resolves — pass `article_id` directly in that case

**Example — no article_id needed**

```bash
curl "http://localhost:8000/v1/lcd/coverage?cpt_code=J9217"
```

```json
{
  "cpt_code_queried": "J9217",
  "article_id": "52453",
  "cpt_hcpcs_codes": [...],
  "icd10_covered_codes": [...],
  "modifier_codes": [],
  "total_cpt_codes": 14,
  "total_icd10_codes": 493,
  "total_modifier_codes": 0
}
```

**Example — with article_id override**

```bash
curl "http://localhost:8000/v1/lcd/coverage?cpt_code=J9271&article_id=52453"
```

**Response codes**

| Code | Meaning |
|---|---|
| `200` | Success |
| `404` | No LCD article found for the CPT code |
| `502` | CMS upstream API error |
| `503` | CMS API unreachable or token refresh failed |

---

### `GET /health`

```json
{ "status": "ok", "service": "CMS LCD Coverage Service", "version": "1.0.0" }
```

### `GET /v1/lcd/token-status`

```json
{
  "has_token": true,
  "expires_at": "2026-02-27T14:30:00+00:00",
  "minutes_remaining": 42.3,
  "is_valid": true
}
```

---

## Configuration

Copy `.env.example` to `.env`. All settings have working defaults.

| Variable | Default | Description |
|---|---|---|
| `CMS_LCD_BASE_URL` | `https://api.coverage.cms.gov/v1` | CMS API base URL |
| `CMS_LICENSE_AMA` | `true` | Accept AMA CPT license |
| `CMS_LICENSE_ADA` | `true` | Accept ADA CDT license |
| `CMS_LICENSE_AHA` | `true` | Accept AHA UB-04 license |
| `TOKEN_REFRESH_BUFFER_MINUTES` | `5` | Refresh token this many minutes before expiry |
| `TOKEN_EXPIRY_MINUTES` | `60` | Assumed token lifetime |
| `HTTP_TIMEOUT_SECONDS` | `30.0` | CMS API request timeout |
| `LOG_LEVEL` | `INFO` | Logging level |

---

## HCPC→Article Mapping

`article_hcpc_mapping.csv` is a pre-built lookup table generated from the [CMS bulk download](https://www.cms.gov/medicare-coverage-database/downloads/downloadable-databases.aspx).

| Stat | Value |
|---|---|
| Articles | 1,115 |
| HCPC code rows | 18,370 |
| Unique HCPC codes | 4,184 |
| Last updated | Feb 23, 2026 |

The service loads this file into memory at startup. This eliminates a CMS network round-trip for most codes.

**To regenerate the mapping** (e.g. after a CMS data release):

```bash
make refresh-mapping
```

This downloads `current_article.zip` from CMS, extracts the CSVs, and rebuilds `article_hcpc_mapping.csv`.

---

## Postman Collection

Import `cms-lcd-service.postman_collection.json` into Postman.

**Folders included:**

| Folder | Description |
|---|---|
| Health | Health check + token status |
| LCD Coverage — No article_id needed | J9217, 82306, custom template (auto-resolve) |
| LCD Coverage — article_id override | J9271, 82306 pinned article, custom template |
| Bulk Lookup (Collection Runner) | CSV-driven sweep of all 18,370 rows |

To run the bulk sweep: **Run collection → select Bulk Lookup folder → Data: `article_hcpc_mapping.csv` → Run**.

---

## Project Structure

```
cms-lcd-service/
├── app/
│   ├── cms_client/
│   │   ├── cms_api.py          # CMS HTTP client (connection pooling, 401 retry)
│   │   └── token_manager.py    # Bearer token lifecycle (lazy fetch, auto-refresh)
│   ├── core/
│   │   ├── config.py           # Pydantic settings (loaded from .env)
│   │   ├── exceptions.py       # Custom exception types
│   │   └── hcpc_lookup.py      # Local HCPC→article mapping from CSV
│   ├── routers/
│   │   ├── coverage.py         # GET /v1/lcd/coverage
│   │   └── health.py           # GET /health, GET /v1/lcd/token-status
│   ├── schemas/
│   │   └── coverage_schemas.py # Pydantic request/response models
│   ├── services/
│   │   └── coverage_service.py # Orchestration: lookup → parallel fetch → response
│   └── main.py                 # FastAPI app, lifespan, middleware
├── article_hcpc_mapping.csv    # HCPC→article lookup table (CMS bulk data)
├── cms-lcd-service.postman_collection.json
├── .env.example
├── Makefile
└── requirements.txt
```

---

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| `fastapi` | 0.115.0 | Web framework |
| `uvicorn[standard]` | 0.30.0 | ASGI server |
| `httpx` | 0.27.0 | Async HTTP client |
| `pydantic` | 2.7.0 | Data validation |
| `pydantic-settings` | 2.3.0 | `.env` config loading |
| `python-dotenv` | 1.0.1 | `.env` file support |

# CMS LCD Coverage Service

A FastAPI microservice that wraps the CMS Local Coverage Determination (LCD) API and returns all coverage data for a CPT/HCPCS code in a **single call** — CPT codes, ICD-10 medical necessity codes, and modifier codes.

Built for **Claims** & **Prior Authorization workflows**: given a procedure code, instantly retrieve every ICD-10 diagnosis that establishes medical necessity.

**Live deployment:** <https://cms-lcd-service.onrender.com> — GUI at <https://cms-lcd-service.onrender.com/ui>, API docs at <https://cms-lcd-service.onrender.com/docs>. (Free tier: the instance may sleep after ~15 min idle; the first request then takes ~30–60 s to wake.)


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

### Run as a persistent service (macOS)

To keep the app **live** — surviving terminal logout, restarting on crash, and starting
automatically at login — install it as a launchd agent:

```bash
make install-service     # install + start (port 8000)
make service-status      # check it
make restart-service     # apply code changes (no --reload in service mode)
make uninstall-service   # remove
```

Server logs: `logs/server.out.log` / `logs/server.err.log` · Decision audit:
`logs/decisions-YYYYMMDD.jsonl`

| URL | Description |
|---|---|
| https://cms-lcd-service.onrender.com/ui | **Web GUI** — J code + billing state → supported ICD-10 DX codes for that jurisdiction, **or** search by article number → that article's full covered-DX list (+ optional dx verification either way) |
| https://cms-lcd-service.onrender.com/docs | Interactive Swagger UI |
| https://cms-lcd-service.onrender.com/redoc | ReDoc documentation |
| https://cms-lcd-service.onrender.com/health | Health check |

---

## API Reference

### `GET /v1/lcd/coverage`

Returns unified LCD coverage data for a CPT/HCPCS code.

**Parameters**

| Parameter | Required | Description |
|---|---|---|
| `cpt_code` | Yes | CPT/HCPCS procedure code (e.g. `J9217`) |
| `article_id` | No | Numeric LCD article ID. Skips lookup if provided. |
| `icd10` | No | Patient's ICD-10 diagnosis. Narrows multi-article codes to the exact governing article. |
| `state` | No | Billing state (abbr or name, e.g. `NY`). Resolves to the article that governs that jurisdiction. |
| `contractor` | No | MAC/contractor name (substring, e.g. `Palmetto`). Resolves to that contractor's article. |

> **Note:** Pass the numeric article ID only (e.g. `52453`, not `A52453`). The CMS API rejects the `A` prefix.

**Article disambiguation (Prior Auth)**

Many CPT/HCPCS codes map to *multiple* LCD articles (e.g. `J1568` maps to 8 IVIG articles).
Pass the patient's diagnosis to get the exact article:

```bash
curl "https://cms-lcd-service.onrender.com/v1/lcd/coverage?cpt_code=J1568&icd10=D69.3"
```

Resolution order:
1. `article_id` provided → used as-is (`article_selection: "caller-provided"`)
2. `icd10` provided → CPT candidates ∩ articles whose covered ICD-10 list contains the
   diagnosis (prefix-aware: `D84.90` matches a listed `D84.9`). A unique match wins; if
   several match, the most specific article (fewest ICD-10 codes) is used.
3. `state`/`contractor` provided → the **applicable local article** for the billing
   jurisdiction (`article_selection: "jurisdiction-local-article"`). When both are given,
   the contractor must serve that state. This step exists because the same J code maps
   to one article per MAC and their covered-diagnosis lists differ — resolving to the
   wrong MAC's article is a denial cause (see "Jurisdiction" below)
4. A direct **coverage policy article** (CMS article type 1/4) among the candidates
5. The **governing policy article** resolved from "Billing and Coding" companion
   articles (ICD-10 fingerprint + majority vote). Companions are coding references, not
   coverage determinations — e.g. all 8 `J1568` companions resolve to policy article
   `52509` (Intravenous Immune Globulin)
6. First local match (legacy behavior, flagged)

The response always includes `article_selection` (how the article was resolved),
`coverage_source` (the official CMS NCD citation when CMS links the code to one, e.g.
`NCD 158, NCD Manual §250.3 — ...`), and `candidate_articles` (all articles mapping to
the code, with type/status/effective date, NCD link, governing article, and an
`icd10_covered` flag) whenever more than one article matches.

If **no** article covers the diagnosis, `article_selection` reads
`no-candidate-covers-icd10` — the claim may be governed by an **NCD** (e.g. NCD 250.3 for
IVIG), which is not part of the MCD article database. Verify against the payer's policy
before filing.

**Jurisdiction (state / contractor)**

Many J codes map to one LCD article *per MAC/contractor*, and each article carries its
own covered-ICD-10 list. A diagnosis can be valid under one contractor's article and
absent from the contractor that actually governs the billing state — a claim filed with
the wrong list is denied even though the diagnosis is "valid nationally".

Pass the billing `state` (and optionally the payer `contractor`):

```bash
# New York claim: resolves to A59105 (the NY Part B article), not A56718 (Palmetto)
curl "https://cms-lcd-service.onrender.com/v1/lcd/coverage?cpt_code=J1568&state=NY"
```

- The response `jurisdiction` field states the scope applied (e.g. `jurisdiction: New York`)
- `excluded_articles` lists articles that map to the code but do **not** apply in that
  jurisdiction — do not cite these
- `candidate_articles[].in_jurisdiction` flags which candidates apply
- Articles with no contractor scope (national policy articles) always stay in scope
- Unknown state/contractor or a contractor that does not serve the state → the lookup
  falls back to national and the label says so

State sub-jurisdictions are grouped with their parent (e.g. `NY` covers state 41 plus
Downstate/Queens/Upstate 63–65; `CA` covers 6 plus Northern/Southern).

**Article ID resolution (automatic)**

When `article_id` is omitted:
1. Checks `article_hcpc_mapping.csv` locally — instant, no network call
2. Falls back to CMS reverse lookup if not found locally
3. Returns `404` if neither resolves — pass `article_id` directly in that case

**Example — no article_id needed**

```bash
curl "https://cms-lcd-service.onrender.com/v1/lcd/coverage?cpt_code=J9217"
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
  "total_modifier_codes": 0,
  "icd10_queried": null,
  "article_selection": "first-local-match",
  "candidate_articles": []
}
```

**Example — multi-article code disambiguated by diagnosis**

```bash
curl "https://cms-lcd-service.onrender.com/v1/lcd/coverage?cpt_code=J1568&icd10=D69.3"
```

```json
{
  "cpt_code_queried": "J1568",
  "article_id": "57160",
  "icd10_queried": "D69.3",
  "article_selection": "icd10-match (8 articles cover this diagnosis; most specific used — verify via candidate_articles)",
  "candidate_articles": [
    {"article_id": "57160", "title": "Billing and Coding: Immune Thrombocytopenia (ITP) Therapy", "icd10_covered": true},
    {"article_id": "56718", "title": "Billing and Coding: Intravenous Immunoglobulin (IVIG)", "icd10_covered": true}
  ]
}
```

**Example — with article_id override**

```bash
curl "https://cms-lcd-service.onrender.com/v1/lcd/coverage?cpt_code=J9271&article_id=52453"
```

**Response codes**

| Code | Meaning |
|---|---|
| `200` | Success |
| `404` | No LCD article found for the CPT code |
| `502` | CMS upstream API error |
| `503` | CMS API unreachable or token refresh failed |

---

### `GET /v1/lcd/dx-codes`

**Direct J code → ICD-10 diagnosis lookup** for PA filing. Returns every diagnosis code
that any CMS coverage document tied to the J code supports, with per-article source
attribution. Fully local — no CMS API calls, instant.

| Parameter | Required | Description |
|---|---|---|
| `cpt_code` | Yes | CPT/HCPCS procedure code (e.g. `J1568`) |
| `state` | No | Billing state (abbr or name, e.g. `NY`). Scope the union to the articles that govern that state. |
| `contractor` | No | MAC/contractor name (substring, e.g. `Palmetto`). Scope to that contractor's articles. |

```bash
# All of them (national union)
curl "https://cms-lcd-service.onrender.com/v1/lcd/dx-codes?cpt_code=J1568"
# Only the diagnoses the New York article accepts
curl "https://cms-lcd-service.onrender.com/v1/lcd/dx-codes?cpt_code=J1568&state=NY"
```

Every article in `qualified_articles` (and in the GUI's Qualified-articles bar and
source chips) carries a `cms_url` — the direct link to that article's page in the
[Medicare Coverage Database](https://www.cms.gov/medicare-coverage-database), e.g.
`https://www.cms.gov/medicare-coverage-database/view/article.aspx?articleid=59105`.
In the GUI these article numbers are hyperlinks (open in a new tab).

Group semantics: **group 1 = medical necessity** (the list shown on the CMS article
page); other groups are additional code lists the article carries. The GUI summary
leads with the group-1 count for exactly this reason.

```json
{
  "cpt_code": "J1568",
  "total_dx_codes": 1446,
  "dx_codes": [
    {
      "code": "D69.3",
      "description": "Immune thrombocytopenic purpura",
      "sources": [
        {"article_id": "57160", "group": "1"},
        {"article_id": "52509", "group": "1"}
      ]
    }
  ],
  "candidate_articles": [ ... ]
}
```

### `GET /v1/lcd/dx`

**Verify one diagnosis against a J code** — answers "is this patient's dx supported for
this drug?" before you file.

| Parameter | Required | Description |
|---|---|---|
| `cpt_code` | Yes | CPT/HCPCS procedure code |
| `icd10` | Yes | Patient's ICD-10 diagnosis (prefix-aware: `D84.90` matches listed `D84.9`) |
| `state` | No | Billing state (abbr or name, e.g. `NY`). Evaluate against the article that governs that state. |
| `contractor` | No | MAC/contractor name (substring, e.g. `Palmetto`). |
| `article_id` | No | **Article mode** — verify the dx against one specific article number instead of a J code (`cpt_code` can then be omitted). |

```bash
curl "https://cms-lcd-service.onrender.com/v1/lcd/dx?cpt_code=J1568&icd10=D69.3"
# The PA question: is this dx accepted where the claim will actually be filed?
curl "https://cms-lcd-service.onrender.com/v1/lcd/dx?cpt_code=J1568&icd10=D61.818&state=NY"
# Payer cited article A59105 in the denial — is the dx in that article's list?
curl "https://cms-lcd-service.onrender.com/v1/lcd/dx?article_id=59105&icd10=D61.818"
```

```json
{
  "cpt_code": "J1568",
  "icd10_queried": "D69.3",
  "supported": true,
  "code_known": true,
  "supporting_articles": [ ... ],
  "noncovered_in": [],
  "verdict": "SUPPORTED — covered by 8 article(s); cite [...] on the PA"
}
```

Verdicts:
- `SUPPORTED` — covered by at least one in-scope article; cite the listed article(s)
- `CAUTION` — covered by some articles but explicitly non-covered by others
- **`NOT SUPPORTED IN THIS JURISDICTION`** — covered nationally but **not** by the
  article that applies in the billing `state`/`contractor`. This is the exact failure
  mode behind real denials (e.g. `J1568` + `D61.818`: present in Palmetto's article
  `56718`, absent from the New York article `59105`). Do not file with this dx — pick
  one from the local article's list
- `NOT SUPPORTED` — no article covers the dx (off-label or NCD-governed; verify)
- unknown code warning

### `GET /v1/lcd/article`

**Search one LCD article by article number** — the way payers reference policies in
denials (e.g. `A59105`). Returns, fully locally:

- title, type, status, effective/end dates
- official NCD link (if CMS ties the article to one) and the governing policy article
- MAC/contractors and the **states they serve**
- every J/HCPCS/CPT code mapped to the article
- **every ICD-10 code the article lists as covered** (with group attribution)

| Parameter | Required | Description |
|---|---|---|
| `article_id` | Yes | Article number, with or without the `A` prefix |

```bash
curl "https://cms-lcd-service.onrender.com/v1/lcd/article?article_id=59105"
```

Workflow: denial cites an article number → look it up here → pick a dx from *its*
covered list (or verify your dx with `GET /v1/lcd/dx?article_id=...&icd10=...`).

### `GET /v1/lcd/states`

Returns the list of resolvable jurisdictions (`name`, `abbr`, `state_ids`) used to
populate `state` parameters and the GUI dropdown.

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

Two pre-built lookup tables generated from the [CMS bulk download](https://www.cms.gov/medicare-coverage-database/downloads/downloadable-databases.aspx):

**`article_hcpc_mapping.csv`** — HCPC code → article candidates, with article metadata
(type, status, effective/end dates).

| Stat | Value |
|---|---|
| Articles | 1,092 |
| HCPC code rows | 18,582 |
| Last updated | Sep 22, 2026 |

**`icd10_dx_index.csv`** — every ICD-10 ↔ article pair (covered + explicitly non-covered,
~490k rows) and **`icd10_descriptions.csv`** (unique code descriptions). Together these
power the J-code → diagnosis endpoints without any network call.

**`article_governing_map.csv`** — for each "Billing and Coding" companion article, the
governing coverage policy article (ICD-10 fingerprint) and the official CMS NCD link
(section + title from the NCD bulk download).

**Jurisdiction tables** — `state_reference.csv` (state_id → name/abbr, with sub-state
grouping), `contractor_states.csv` (contractor_id → states it serves), and
`contractor_reference.csv` (contractor_id → MAC name). These power `state`/`contractor`
scoping: state → the contractors that serve it → the articles scoped to those
contractors.

The service loads all files into memory at startup.

This eliminates CMS network round-trips for most codes and makes article resolution
instant.

**To regenerate the mapping** (e.g. after a CMS data release):

```bash
make refresh-mapping
```

This downloads the CMS article + NCD bulk files, extracts the CSVs, and rebuilds
`icd10_article_index.csv`, `article_governing_map.csv`, `article_hcpc_mapping.csv` (now
with per-article `contractor_ids`/`contractor_names`), plus the jurisdiction tables
(`contractor_reference.csv`, `contractor_states.csv`, `state_reference.csv`).

---

## Decision Audit Log

Every article-resolution and dx-verification decision is appended to
`logs/decisions-YYYYMMDD.jsonl` (one JSON line per call) with the timestamp, endpoint,
J code, state/contractor, ICD-10, the resolved article, the selection rule, the
jurisdiction label, the NCD coverage source, and the dx verdict. This is the evidence
trail for a denial: it records *exactly which article and jurisdiction the decision was
made from*. Audit writes never block or fail the request.

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
│   ├── static/
│   │   └── dx_lookup.html      # Web GUI served at GET /ui
│   ├── cms_client/
│   │   ├── cms_api.py          # CMS HTTP client (connection pooling, 401 retry)
│   │   └── token_manager.py    # Bearer token lifecycle (lazy fetch, auto-refresh)
│   ├── core/
│   │   ├── audit.py            # JSONL decision audit log (logs/decisions-*.jsonl)
│   │   ├── config.py           # Pydantic settings (loaded from .env)
│   │   ├── exceptions.py       # Custom exception types
│   │   └── hcpc_lookup.py      # HCPC→article + ICD-10→article + state/contractor indices
│   ├── routers/
│   │   ├── coverage.py         # GET /v1/lcd/coverage
│   │   ├── dx.py               # GET /v1/lcd/dx-codes, /v1/lcd/dx, /v1/lcd/states
│   │   └── health.py           # GET /health, GET /v1/lcd/token-status
│   ├── schemas/
│   │   └── coverage_schemas.py # Pydantic request/response models
│   ├── services/
│   │   └── coverage_service.py # Orchestration: lookup → parallel fetch → response
│   └── main.py                 # FastAPI app, lifespan, middleware
├── article_hcpc_mapping.csv    # HCPC→article table (incl. per-article contractor_ids)
├── icd10_dx_index.csv          # ICD-10→article covered/non-covered index (CMS bulk data)
├── icd10_descriptions.csv      # ICD-10 code → description
├── article_governing_map.csv   # companion→policy article + NCD links (CMS bulk data)
├── state_reference.csv         # state_id → name/abbr, sub-state groups (NY, CA, MO)
├── contractor_states.csv       # contractor_id → states it serves
├── contractor_reference.csv    # contractor_id → MAC name
├── logs/                       # JSONL decision audit log (created at runtime)
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

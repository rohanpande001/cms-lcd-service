# CMS LCD DX Lookup — App Documentation

> **One-liner:** A local web app + API for MedOnc Prior Auth filing. Enter a J code (+ billing state) and get the exact ICD-10 diagnosis codes that the governing CMS coverage article accepts — before you file, not after a denial.

**Live at:** `http://localhost:8000/ui` · **API docs:** `http://localhost:8000/docs`

---

## 1. The problem this solves

Prior Auth denials were happening because diagnosis codes were picked from the **wrong coverage article**:

> **Incident (Octagam, J1568):** DX `D61.818` was taken from article **A56718** (Palmetto GBA — serves SC, TX, AL, …). The claim was filed for **New York**, where the governing article is **A59105** (Wellpoint Federal / NGS). A59105 does **not** list D61.818 → claim denied. The diagnosis was "valid nationally" but invalid for the billing jurisdiction.

Root causes:
1. The J code was looked up **without** state / MAC / payer context.
2. One article was picked even when 8 existed nationally.
3. Diagnoses were merged into a global J-code list **without jurisdiction scope**.
4. The decision (which article, which list, why) was **not persisted** — so denials couldn't be traced.

This app fixes all four.

---

## 2. How it works — the core algorithm

Given **J code + billing state** (and optionally MAC/contractor + diagnosis):

```
Step 1  J code → candidate articles
        CMS's official article×HCPCS crosswalk says which articles list J1568.
        (J1568 → 8 "Billing and Coding" companion articles, one per MAC)

Step 2  Article → contractor (MAC)
        CMS's article×contractor table maps each article to its MAC
        (59105 → Wellpoint Federal, 56718 → Palmetto GBA, …)

Step 3  State → contractors that serve it
        CMS per-state coverage reports say which MACs bill that state
        (NY → NGS + Wellpoint Federal)

Step 4  Intersect + keep type 6 only
        Articles whose contractor serves the state, and which are
        "Billing and Coding" companions (CMS article type 6) —
        the documents whose ICD-10 lists the payer actually applies.
        National policy/NCD articles (type 1/4) are reference only.

Step 5  Build the diagnosis list
        The covered ICD-10 codes of the qualified article(s), with group
        attribution. Group 1 = medical necessity (what the CMS website
        shows); other groups = additional code lists the article carries.
```

**Worked example — J1568 + New York:**
candidates (8) → NY contractors (NGS, Wellpoint Federal) → only **A59105** qualifies → **79 group-1 (medical necessity) codes** — exactly the list on the CMS website.

**The denial guard:** if you check a DX that another MAC's article covers but the local one doesn't, the verdict says:

> `NOT SUPPORTED IN THIS JURISDICTION [New York] — covered by another MAC's billing article (56718) but NOT by the applicable article (59105). Filing this dx will likely be denied.`

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Browser  →  GUI (app/static/dx_lookup.html — vanilla JS, no deps)│
└──────────────────────────┬───────────────────────────────────────┘
                           │ HTTP/JSON
┌──────────────────────────▼───────────────────────────────────────┐
│  FastAPI app (app/main.py)  ·  uvicorn on port 8000               │
│                                                                   │
│  ┌─ routers ──────────────────────────────────────────────────┐  │
│  │  dx.py        /v1/lcd/dx-codes · /v1/lcd/dx                │  │
│  │                 /v1/lcd/article · /v1/lcd/states           │  │
│  │  coverage.py  /v1/lcd/coverage (live CMS fetch)            │  │
│  │  health.py    /health · /v1/lcd/token-status               │  │
│  └────────────────────────────────────────────────────────────┘  │
│  ┌─ services ─────────────────────────────────────────────────┐  │
│  │  coverage_service.py — article resolution orchestration     │  │
│  │  (caller article_id → ICD-10 match → jurisdiction-local →  │  │
│  │   direct policy → governing-policy majority → first)       │  │
│  └────────────────────────────────────────────────────────────┘  │
│  ┌─ core ─────────────────────────────────────────────────────┐  │
│  │  hcpc_lookup.py — in-memory lookup engine (all local data) │  │
│  │  audit.py         — JSONL decision log                     │  │
│  │  config.py / exceptions.py                                 │  │
│  └────────────────────────────────────────────────────────────┘  │
│  ┌─ cms_client ───────────────────────────────────────────────┐  │
│  │  cms_api.py — httpx client (pooling, 401 retry)            │  │
│  │  token_manager.py — 60-min CMS Bearer token, auto-refresh  │  │
│  └────────────────────────────────────────────────────────────┘  │
└───────────────┬──────────────────────────────┬───────────────────┘
                │                              │
     ┌──────────▼──────────┐        ┌──────────▼───────────────────┐
     │  CSV data files      │        │  CMS Coverage API             │
     │  (repo root, built   │        │  api.coverage.cms.gov — used  │
     │  from CMS bulk +     │        │  ONLY by /v1/lcd/coverage     │
     │  CMS API, in-memory) │        │  (token auto-managed)         │
     └──────────────────────┘        └──────────────────────────────┘
```

**Key design choices**
- **DX endpoints are 100% local** (no network calls) — instant, and they work even when CMS is down. Only `/v1/lcd/coverage` hits the live CMS API (full article CPT/ICD-10/modifier fetches).
- **No `--reload` in production mode** — code changes require a service restart.
- **Every resolution decision is audited** (section 7).

---

## 4. Is a database used?

> **No.** There is **no SQL or NoSQL database** (no PostgreSQL, MySQL, SQLite, MongoDB, Redis, …).

Storage is **flat files**:

| Role | Format | Where |
|---|---|---|
| Reference data (the "database") | **CSV**, loaded into in-memory Python dicts at startup | repo root (section 5) |
| Decision audit trail | **JSONL** (append-only, one line per decision, per day) | `logs/decisions-YYYYMMDD.jsonl` |
| Server logs | plain text | `logs/server.out.log`, `logs/server.err.log` |
| Runtime state (CMS token, caches) | in-memory only | — |

**Why not a real DB?**
- The data is read-mostly reference data (~1 MB of dicts in RAM), refreshed a few times a year from CMS.
- Single machine, single process, no concurrent writers — CSV + in-memory index is simpler, faster to load, and trivially rebuildable (`make refresh-mapping`).
- If multi-user writes, historical versioning, or scale ever become needs, the CSVs map 1:1 to tables and can be migrated without redesigning the API.

### Data files (the de-facto database)

| File | Rows | Contents |
|---|---|---|
| `article_hcpc_mapping.csv` | 18,582 | J/HCPCS/CPT code → candidate articles, with metadata + per-article `contractor_ids`/`contractor_names` |
| `icd10_dx_index.csv` | 491,662 | Every ICD-10 ↔ article pair, covered (Y) or explicitly non-covered (N), with group number |
| `icd10_descriptions.csv` | 56,372 | ICD-10 code → description |
| `article_governing_map.csv` | 1,945 | Billing companion → governing policy article + official NCD link |
| `article_reference.csv` | 2,057 | Metadata for every article (incl. policy articles without J-code links) |
| `contractor_reference.csv` | 144 | contractor_id → MAC name |
| `contractor_states.csv` | 144 | contractor_id → states it serves |
| `state_reference.csv` | 63 | state_id → name/abbr, sub-state groups (NY = 41+63/64/65, CA = 6+66/67, MO = 29+61/62) |

**Data sources & refresh:**
- CMS **bulk download** (`current_article.zip`, `ncd.zip`) → the big files above
- CMS **Coverage API** (per-state reports + contractor metadata) → the three small jurisdiction files
- Rebuild everything: `make refresh-mapping` (downloads, extracts, re-runs all `scripts/build_*.py`)

---

## 5. API reference

| Endpoint | Purpose | Network? |
|---|---|---|
| `GET /v1/lcd/dx-codes?cpt_code=J1568&state=NY&contractor=` | All ICD-10 codes the qualified billing article(s) cover, with per-article + group attribution. Returns `qualified_articles` (the exact type-6 articles, with `cms_url` links) and a group-1 count | Local |
| `GET /v1/lcd/dx?cpt_code=J1568&icd10=D61.818&state=NY` | Verify one diagnosis → verdict: SUPPORTED / CAUTION / **NOT SUPPORTED IN THIS JURISDICTION** / NOT SUPPORTED | Local |
| `GET /v1/lcd/dx?article_id=59105&icd10=D61.818` | Article mode: verify a dx against one specific article number (payers cite articles in denials) | Local |
| `GET /v1/lcd/article?article_id=59105&state=NY` | Full article profile: title/type/dates, NCD link, contractors, states served, J codes, complete covered-DX list; optional state applicability check | Local |
| `GET /v1/lcd/coverage?cpt_code=J1568&state=NY` | Full article data (CPT codes, ICD-10, modifiers) via live CMS API, jurisdiction-aware resolution | Live CMS |
| `GET /v1/lcd/states` | Jurisdictions for the `state` param / GUI dropdown | Local |
| `GET /health`, `GET /v1/lcd/token-status` | Ops endpoints | Local |

**Article resolution priority** (`/v1/lcd/coverage`):
1. `article_id` provided → used as-is
2. `icd10` provided → candidates ∩ articles covering the diagnosis (prefix-aware: `D84.90` matches `D84.9`)
3. `state`/`contractor` provided → the applicable **local** billing article (never the national policy — this was the denial cause)
4. Direct coverage-policy article (type 1/4)
5. Governing policy article (majority fingerprint vote)
6. First match (flagged) → CMS reverse-lookup fallback

Every response carries `article_selection` (how it was resolved), `jurisdiction`, `excluded_articles`, and `cms_url` links.

---

## 6. GUI workflows (`/ui`)

- **J-code mode:** J code + billing state → *Find DX codes* → group-1 headline count, qualified-articles bar (hyperlinked to CMS), filterable table, *Copy CSV*.
- **Article mode:** article number (e.g. `A59105`) → *Lookup article* → the article's own jurisdiction, J codes, and its covered-DX list. With a state selected, it also tells you ✓/✗ whether the article applies there.
- **Verify:** any ICD-10 + (J code or article) → *Check* → SUPPORTED / CAUTION / NOT SUPPORTED verdict box.
- Group filter (Group 1 = medical necessity), text filter, CSV copy on the filtered view.
- All article numbers are hyperlinks to the CMS Medicare Coverage Database.

---

## 7. Audit trail (denial forensics)

Every decision appends one JSON line to `logs/decisions-YYYYMMDD.jsonl`:

```json
{"ts":"2026-09-23T08:32:58Z","endpoint":"/v1/lcd/dx","cpt_code":"J1568","state":"NY",
 "contractor":null,"icd10":"D61.818","article_id":"59105","selection":"jurisdiction-scoped",
 "jurisdiction":"jurisdiction: New York","coverage_source":null,"supported":false,
 "verdict":"NOT SUPPORTED IN THIS JURISDICTION ..."}
```

If a claim is denied, the exact article, jurisdiction, selection rule, and verdict used at filing time are recoverable. Audit writes never block or fail a request.

---

## 8. Operations

- **Runs as a macOS launchd service** (`com.cmslcd.service`) — starts at login, auto-restarts on crash, survives logout.
- `make install-service` / `make service-status` / `make restart-service` / `make uninstall-service`
- Data refresh: `make refresh-mapping` (CMS bulk + API rebuilds of all tables)
- CMS token: 60-minute Bearer token, fetched lazily and auto-refreshed 5 min before expiry — only needed for `/v1/lcd/coverage`.

---

## 9. Known limitations

- `/data/article/modifier` (CMS API) returns HTTP 400 for many articles → modifier lists come back empty (logged, non-fatal).
- Article search resolves the **current version** of each article; effective-date-of-service filtering is not implemented.
- Data is as fresh as the last `make refresh-mapping` run.
- The CMS website blocks non-browser clients (HTTP 403 to curl) — article hyperlinks work in a real browser.

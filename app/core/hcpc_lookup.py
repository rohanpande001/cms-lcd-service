import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Data files live at the project root
_ROOT = Path(__file__).resolve().parents[2]
_MAPPING_PATH = _ROOT / "article_hcpc_mapping.csv"
_DX_INDEX_PATH = _ROOT / "icd10_dx_index.csv"
_DESC_PATH = _ROOT / "icd10_descriptions.csv"
_ARTICLE_REF_PATH = _ROOT / "article_reference.csv"
_STATES_PATH = _ROOT / "state_reference.csv"
_CONTRACTOR_STATES_PATH = _ROOT / "contractor_states.csv"
_CONTRACTOR_REF_PATH = _ROOT / "contractor_reference.csv"

# hcpc_code -> list of candidate article dicts (CSV order, deduped by article_id)
_candidates: dict[str, list[dict]] = {}

# icd10_code -> list of {"article_id","covered","group"} entries (all articles)
_dx_entries: dict[str, list[dict]] = {}

# icd10_code -> set of article_ids covering it (covered=Y only)
_icd10_index: dict[str, set[str]] = {}

# article_id -> number of covered ICD-10 codes (specificity signal)
_icd10_counts: dict[str, int] = {}

# icd10_code -> description
_dx_descriptions: dict[str, str] = {}

# article_id -> metadata dict (first mapping row wins); article_id -> hcpc codes
_article_meta: dict[str, dict] = {}
_article_hcpcs: dict[str, set[str]] = {}
# article_id -> metadata for EVERY article (fallback for policy articles without HCPC links)
_article_ref: dict[str, dict] = {}

# state_id -> set of contractor_ids serving that state
_state_contractors: dict[str, set[str]] = {}
# state_id -> abbr
_state_abbrs: dict[str, str] = {}

# state query (abbr or name, upper) -> (state_ids, display name)
_state_queries: dict[str, tuple[list[str], str]] = {}

# contractor_id -> name
_contractor_names: dict[str, str] = {}

# Cache: frozenset(article_ids) -> dx_codes list (same J codes repeat in production)
_dx_union_cache: dict[frozenset[str], list[dict]] = {}
_DX_CACHE_MAX = 512


def _clean(value: str) -> str:
    return (value or "").strip().strip('"')


def _load_mapping() -> None:
    if not _MAPPING_PATH.exists():
        logger.warning("article_hcpc_mapping.csv not found at %s — local lookup disabled", _MAPPING_PATH)
        return

    csv.field_size_limit(10 * 1024 * 1024)
    with open(_MAPPING_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = _clean(row.get("hcpc_code", "")).upper()
            article_id = _clean(row.get("article_id", ""))
            if not code or not article_id:
                continue
            entry = {
                "article_id": article_id,
                "display_id": _clean(row.get("display_id", "")),
                "article_type": _clean(row.get("article_type", "")),
                "article_title": _clean(row.get("article_title", "")),
                "status": _clean(row.get("status", "")),
                "eff_date": _clean(row.get("article_eff_date", "")),
                "end_date": _clean(row.get("article_end_date", "")),
                "ncd_id": _clean(row.get("ncd_id", "")),
                "ncd_section": _clean(row.get("ncd_section", "")),
                "ncd_title": _clean(row.get("ncd_title", "")),
                "governing_article_id": _clean(row.get("governing_article_id", "")),
                "contractor_ids": [c for c in _clean(row.get("contractor_ids", "")).split(",") if c],
                "contractor_names": [n for n in _clean(row.get("contractor_names", "")).split("|") if n],
            }
            bucket = _candidates.setdefault(code, [])
            if not any(e["article_id"] == article_id for e in bucket):
                bucket.append(entry)
            _article_hcpcs.setdefault(article_id, set()).add(code)
            if article_id not in _article_meta:
                _article_meta[article_id] = {
                    "article_id": article_id,
                    "display_id": entry["display_id"],
                    "article_type": entry["article_type"],
                    "article_title": entry["article_title"],
                    "status": entry["status"],
                    "eff_date": entry["eff_date"],
                    "end_date": entry["end_date"],
                    "ncd_id": entry["ncd_id"],
                    "ncd_section": entry["ncd_section"],
                    "ncd_title": entry["ncd_title"],
                    "governing_article_id": entry["governing_article_id"],
                    "contractor_ids": entry["contractor_ids"],
                    "contractor_names": entry["contractor_names"],
                }

    logger.info(
        "Local HCPC→article lookup loaded: %d codes across %d unique article IDs (source: %s)",
        len(_candidates),
        len({e["article_id"] for bucket in _candidates.values() for e in bucket}),
        _MAPPING_PATH.name,
    )


def _load_article_ref() -> None:
    if not _ARTICLE_REF_PATH.exists():
        logger.warning(
            "article_reference.csv not found at %s — article-number search limited to "
            "articles with HCPC links (run `make refresh-mapping`)",
            _ARTICLE_REF_PATH,
        )
        return
    with open(_ARTICLE_REF_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            aid = _clean(row.get("article_id", ""))
            if not aid:
                continue
            _article_ref[aid] = {
                "article_id": aid,
                "display_id": _clean(row.get("display_id", "")),
                "article_type": _clean(row.get("article_type", "")),
                "article_title": _clean(row.get("title", "")),
                "status": _clean(row.get("status", "")),
                "eff_date": _clean(row.get("article_eff_date", "")),
                "end_date": _clean(row.get("article_end_date", "")),
                "ncd_id": "",
                "ncd_section": "",
                "ncd_title": "",
                "governing_article_id": "",
                "contractor_ids": [],
                "contractor_names": [],
            }
    logger.info("Article reference loaded: %d articles", len(_article_ref))


def _load_dx_index() -> None:
    if not _DX_INDEX_PATH.exists():
        logger.warning(
            "icd10_dx_index.csv not found at %s — diagnosis resolution disabled "
            "(run `make refresh-mapping`)",
            _DX_INDEX_PATH,
        )
        return

    if _DESC_PATH.exists():
        with open(_DESC_PATH, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                code = _clean(row.get("icd10_code", "")).upper()
                if code:
                    _dx_descriptions[code] = _clean(row.get("description", ""))

    csv.field_size_limit(10 * 1024 * 1024)
    with open(_DX_INDEX_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = _clean(row.get("icd10_code", "")).upper()
            article_id = _clean(row.get("article_id", ""))
            covered = _clean(row.get("covered", "Y"))
            group = _clean(row.get("group", ""))
            if not code or not article_id:
                continue
            entry = {"article_id": article_id, "covered": covered, "group": group}
            _dx_entries.setdefault(code, []).append(entry)
            if covered == "Y":
                _icd10_index.setdefault(code, set()).add(article_id)
                _icd10_counts[article_id] = _icd10_counts.get(article_id, 0) + 1

    logger.info(
        "ICD-10 index loaded: %d unique codes, %d articles, %d descriptions",
        len(_dx_entries), len(_icd10_counts), len(_dx_descriptions),
    )


def _load_jurisdiction() -> None:
    if not _STATES_PATH.exists():
        logger.warning("state_reference.csv not found — jurisdiction filtering disabled")
        return

    # state_id -> abbr/name; group sub-jurisdictions with their parent
    by_id: dict[str, dict] = {}
    with open(_STATES_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_id[row["state_id"].strip()] = row

    for sid, row in by_id.items():
        name = row["state_name"].strip()
        base = name.split(" - ")[0]
        abbr = row.get("abbr", "").strip()
        group = row.get("state_group", "").strip()
        if abbr:
            _state_abbrs[sid] = abbr
        ids: list[str]
        display = name
        if group:
            ids = [g for g in by_id.values() if g.get("state_group") == group]
            ids = sorted([g["state_id"].strip() for g in ids], key=int)
            display = base
        else:
            ids = [sid]
        for key in {abbr, base.upper()}:
            if key:
                _state_queries.setdefault(key, (ids, display))
    # whole-state alias: e.g. "NEW YORK" already covered by base name

    if _CONTRACTOR_STATES_PATH.exists():
        with open(_CONTRACTOR_STATES_PATH, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                cid = row["contractor_id"].strip()
                for sid in row["states"].split(";"):
                    if sid.strip():
                        _state_contractors.setdefault(sid.strip(), set()).add(cid)

    if _CONTRACTOR_REF_PATH.exists():
        with open(_CONTRACTOR_REF_PATH, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                _contractor_names[row["contractor_id"].strip()] = row["contractor_name"].strip()

    logger.info(
        "Jurisdiction data loaded: %d state queries, %d state→contractor links, %d contractors",
        len(_state_queries), len(_state_contractors), len(_contractor_names),
    )


def find_article_ids(hcpc_code: str) -> list[str]:
    """Return all article IDs that govern the given HCPC/CPT code, or []."""
    return [e["article_id"] for e in _candidates.get(hcpc_code.upper(), [])]


def find_candidates(hcpc_code: str) -> list[dict]:
    """Return candidate article dicts (with metadata) for the given HCPC/CPT code."""
    return _candidates.get(hcpc_code.upper(), [])


def resolve_state(state_query: str) -> tuple[list[str], str] | None:
    """Resolve a state abbreviation or name to (state_ids, display name)."""
    q = (state_query or "").strip().upper()
    if not q:
        return None
    if q in _state_queries:
        return _state_queries[q]
    # try matching against base names (e.g. "New York"), word-boundary safe
    for key, (ids, display) in _state_queries.items():
        if key == q.replace(" ", "") or q.startswith(key + " "):
            return ids, display
    return None


def resolve_contractor(contractor_query: str) -> tuple[set[str], str] | None:
    """Resolve a contractor/MAC name (case-insensitive substring) to contractor IDs."""
    q = (contractor_query or "").strip().upper()
    if not q:
        return None
    matches = {
        cid for cid, name in _contractor_names.items()
        if q in name.upper()
    }
    if not matches:
        return None
    names = sorted({ _contractor_names[c] for c in matches })
    return matches, names[0] if len(names) == 1 else " & ".join(names)


def filter_by_jurisdiction(
    candidates: list[dict],
    state_query: str | None = None,
    contractor_query: str | None = None,
) -> tuple[list[dict], list[dict], str]:
    """
    Split candidates into (local, excluded) for the given jurisdiction.

    Returns (local, excluded, jurisdiction_label). If no jurisdiction filter
    is applied or it matches nothing, local=candidates with a fallback label.
    """
    state_cids: set[str] | None = None
    contractor_cids: set[str] | None = None
    labels: list[str] = []

    state = resolve_state(state_query) if state_query else None
    if state:
        state_ids, display = state
        state_cids = set()
        for sid in state_ids:
            state_cids |= _state_contractors.get(sid, set())
        labels.append(display)

    contractor = resolve_contractor(contractor_query) if contractor_query else None
    if contractor:
        contractor_cids = set(contractor[0])
        labels.append(contractor[1])

    if state_cids is not None and contractor_cids is not None:
        # Both given: the contractor must actually serve that state
        contractor_ids = state_cids & contractor_cids
        if not contractor_ids:
            return candidates, [], (
                f"jurisdiction: {' / '.join(labels)} "
                f"(contractor does not serve that state — treated as national)"
            )
    elif state_cids is not None:
        contractor_ids = state_cids
    elif contractor_cids is not None:
        contractor_ids = contractor_cids
    else:
        if state_query or contractor_query:
            q = state_query or contractor_query
            return candidates, [], f"unknown jurisdiction '{q}' (treated as national)"
        return candidates, [], "national (all jurisdictions)"

    label = f"jurisdiction: {' / '.join(labels)}"
    # Articles with no contractor scope are national policy articles —
    # they apply in every jurisdiction, so they always stay in scope.
    local = [
        c for c in candidates
        if not c.get("contractor_ids")
        or (set(c["contractor_ids"]) & contractor_ids)
    ]
    if not local:
        return candidates, [], label + " (no local articles — national fallback)"
    excluded = [c for c in candidates if c not in local]
    return local, excluded, label


def scoped_article_ids(
    hcpc_code: str,
    state_query: str | None = None,
    contractor_query: str | None = None,
) -> tuple[list[str], list[dict], list[dict], str]:
    """
    Jurisdiction-scoped article IDs for a J/CPT code.

    Returns (article_ids, local, excluded, jurisdiction_label) where
    article_ids = local candidates + their governing policy articles
    (governing articles are national policy and stay in scope).
    """
    candidates = _candidates.get(hcpc_code.upper(), [])
    local, excluded, label = filter_by_jurisdiction(
        candidates, state_query=state_query, contractor_query=contractor_query
    )
    ids: list[str] = []
    for c in local:
        if c["article_id"] not in ids:
            ids.append(c["article_id"])
        gov = c.get("governing_article_id", "")
        if gov and gov not in ids:
            ids.append(gov)
    return ids, local, excluded, label


def normalize_article_id(value: str) -> str:
    """Normalize an article id: trim, uppercase, strip the 'A' prefix (A59105 -> 59105)."""
    v = (value or "").strip().upper()
    if v.startswith("A") and v[1:].isdigit():
        v = v[1:]
    return v


def get_article(article_id: str) -> dict | None:
    """Metadata + mapped J/HCPCS codes for an article, or None if unknown."""
    aid = normalize_article_id(article_id)
    meta = _article_meta.get(aid) or _article_ref.get(aid)
    if meta is None:
        return None
    meta = dict(meta)
    meta["hcpc_codes"] = sorted(_article_hcpcs.get(aid, set()))
    return meta


def article_dx_codes(article_id: str) -> list[dict]:
    """Every covered ICD-10 code listed by one article, with group attribution."""
    aid = normalize_article_id(article_id)
    out: list[dict] = []
    for code, entries in _dx_entries.items():
        for e in entries:
            if e["article_id"] == aid and e["covered"] == "Y":
                out.append({
                    "code": code,
                    "description": _dx_descriptions.get(code, ""),
                    "group": e["group"],
                })
    return sorted(out, key=lambda r: r["code"])


def article_applies_in_state(article_id: str, state_query: str) -> bool | None:
    """
    Does the article apply in the given state?

    Returns True/False, or None when the state could not be resolved.
    National (non-contractor-scoped) articles apply everywhere.
    """
    state = resolve_state(state_query)
    if state is None:
        return None
    meta = _article_meta.get(normalize_article_id(article_id))
    if meta is None:
        return None
    cids = set(meta.get("contractor_ids", []))
    if not cids:
        return True
    state_ids, _display = state
    sids = set()
    for sid in state_ids:
        sids |= _state_contractors.get(sid, set())
    return bool(cids & sids)


def article_states(article_id: str) -> list[str]:
    """State abbreviations served by the article's contractors (sorted)."""
    meta = _article_meta.get(normalize_article_id(article_id))
    if not meta:
        return []
    sids: set[str] = set()
    for cid in meta.get("contractor_ids", []):
        for sid, cids in _state_contractors.items():
            if cid in cids:
                sids.add(sid)
    return sorted({_state_abbrs[s] for s in sids if s in _state_abbrs})


def list_states() -> list[dict]:
    """All resolvable jurisdictions for UI dropdowns: [{name, abbr, state_ids}]."""
    out: dict[str, dict] = {}
    for key, (ids, display) in _state_queries.items():
        rec = out.setdefault(display, {"name": display, "abbr": None, "state_ids": ids})
        if len(key) == 2 and rec["abbr"] is None:
            rec["abbr"] = key
    return sorted(out.values(), key=lambda r: r["name"])


def find_covering_article_ids(icd10_code: str) -> set[str]:
    """
    Return article IDs whose covered ICD-10 list contains the given code.

    Prefix matching lets a specific claim code (e.g. D84.90) match a list
    code (e.g. D84.9): the claim code and each of its prefixes down to
    letter+2-digits are checked against the index.
    """
    code = (icd10_code or "").strip().upper()
    matched: set[str] = set()
    for length in range(len(code), 3, -1):
        matched |= _icd10_index.get(code[:length], set())
    return matched


def icd10_count(article_id: str) -> int:
    """Number of covered ICD-10 codes an article has (specificity signal)."""
    return _icd10_counts.get(article_id, 0)


def _candidate_ids(hcpc_code: str) -> list[str]:
    """Candidate article IDs plus their governing policy articles (for attribution)."""
    candidates = _candidates.get(hcpc_code.upper(), [])
    ids = [c["article_id"] for c in candidates]
    for c in candidates:
        gov = c.get("governing_article_id", "")
        if gov and gov not in ids:
            ids.append(gov)
    return ids


def find_dx_codes(hcpc_code: str, article_ids: list[str] | None = None) -> list[dict]:
    """
    Union of all ICD-10 codes that ANY of the given articles (default: all
    candidates + governing articles for the J code) covers, with per-article
    source attribution. Fully local — no network calls.
    """
    aids = article_ids if article_ids is not None else _candidate_ids(hcpc_code)
    if not aids:
        return []
    key = frozenset(aids)
    cached = _dx_union_cache.get(key)
    if cached is not None:
        return cached

    aid_set = set(aids)
    code_map: dict[str, dict] = {}
    for code, entries in _dx_entries.items():
        for e in entries:
            if e["covered"] == "Y" and e["article_id"] in aid_set:
                rec = code_map.setdefault(
                    code,
                    {"code": code, "description": _dx_descriptions.get(code, ""), "sources": []},
                )
                rec["sources"].append({"article_id": e["article_id"], "group": e["group"]})

    result = sorted(code_map.values(), key=lambda r: r["code"])
    if len(_dx_union_cache) >= _DX_CACHE_MAX:
        _dx_union_cache.clear()
    _dx_union_cache[key] = result
    return result


def check_dx(hcpc_code: str, icd10_code: str, article_ids: list[str] | None = None) -> dict:
    """
    Verify a specific diagnosis against the given articles (default: all
    candidates for the J code).

    Returns:
      supported        — dx appears in a covered list (True/False)
      supporting       — [{"article_id","group"}] for covered matches
      noncovered_in    — [{"article_id"}] where the dx is explicitly NOT covered
      code_known       — dx (or a prefix) exists in the index at all
    """
    code = (icd10_code or "").strip().upper()
    aid_set = set(article_ids) if article_ids is not None else set(_candidate_ids(hcpc_code))
    supporting: dict[str, str] = {}
    noncovered: dict[str, None] = {}
    code_known = False
    for length in range(len(code), 3, -1):
        for e in _dx_entries.get(code[:length], []):
            code_known = True
            if e["article_id"] not in aid_set:
                continue
            if e["covered"] == "Y":
                supporting.setdefault(e["article_id"], e["group"])
            else:
                noncovered.setdefault(e["article_id"], None)
    return {
        "supported": bool(supporting),
        "code_known": code_known,
        "supporting": [
            {"article_id": aid, "group": group} for aid, group in sorted(supporting.items())
        ],
        "noncovered_in": sorted(noncovered),
    }


# Load once at import time
_load_mapping()
_load_article_ref()
_load_dx_index()
_load_jurisdiction()

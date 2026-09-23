"""
Builds contractor_reference.csv — contractor_id, contract_number, contractor_name,
contract_type_id — from the CMS Coverage API for every contractor that appears in
/tmp/article_x_contractor.csv.

Results are cached in /tmp/contractor_ref.json so rebuilds are cheap.

Run via:
    make refresh-mapping

Or manually:
    python scripts/build_contractor_ref.py
"""

import csv
import json
import sys
import urllib.request
from pathlib import Path

csv.field_size_limit(10 * 1024 * 1024)

CONTRACTOR_X_CSV = Path("/tmp/article_x_contractor.csv")
CACHE = Path("/tmp/contractor_ref.json")
OUTPUT = Path(__file__).resolve().parents[1] / "contractor_reference.csv"
BASE_URL = "https://api.coverage.cms.gov/v1"


def get_token() -> str:
    url = f"{BASE_URL}/metadata/license-agreement?ama=true&ada=true&aha=true"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=30))
    return d["data"][0]["Token"]


def fetch_contractor(token: str, contractor_id: int) -> dict | None:
    url = f"{BASE_URL}/data/contractor/?contractor_id={contractor_id}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=30))
        rows = d.get("data", [])
        return rows[0] if rows else None
    except Exception as e:
        print(f"  WARN: contractor {contractor_id}: {e}")
        return None


def main() -> None:
    if not CONTRACTOR_X_CSV.exists():
        print(f"ERROR: {CONTRACTOR_X_CSV} not found. Run `make refresh-mapping` to download first.")
        sys.exit(1)

    ids = sorted({
        int(row["contractor_id"].strip())
        for row in csv.DictReader(open(CONTRACTOR_X_CSV, encoding="utf-8", errors="replace"))
        if (row.get("contractor_id") or "").strip()
    })
    print(f"{len(ids)} distinct contractor IDs to resolve")

    cache: dict[str, dict] = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    token = None
    for cid in ids:
        if str(cid) in cache:
            continue
        if token is None:
            token = get_token()
        row = fetch_contractor(token, cid)
        if row is not None:
            cache[str(cid)] = {
                "contractor_id": cid,
                "contract_number": str(row.get("contract_number", "")),
                "contractor_name": row.get("contractor_name", ""),
                "contract_type_id": row.get("contract_type_id", ""),
            }

    CACHE.write_text(json.dumps(cache, indent=1))

    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["contractor_id", "contract_number", "contractor_name", "contract_type_id"])
        for cid in ids:
            r = cache.get(str(cid), {})
            if r:
                writer.writerow([r["contractor_id"], r["contract_number"], r["contractor_name"], r["contract_type_id"]])

    resolved = sum(1 for cid in ids if str(cid) in cache)
    print(f"Done. {resolved}/{len(ids)} resolved -> {OUTPUT}")


if __name__ == "__main__":
    main()

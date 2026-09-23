"""
Builds contractor_states.csv — which states each contractor (MAC) serves —
derived from the CMS Coverage API's local-coverage-articles report, which
filters articles by jurisdiction (state).

For each state we collect the distinct contractor names that have active
articles there, then join names back to contractor IDs using
contractor_reference.csv (build_contractor_ref.py must run first).

Results are cached in /tmp/state_contractors.json so rebuilds are cheap.

Run via:
    make refresh-mapping

Or manually:
    python scripts/build_contractor_states.py
"""

import csv
import json
import re
import sys
import urllib.request
from pathlib import Path

csv.field_size_limit(10 * 1024 * 1024)

REF_CSV = Path(__file__).resolve().parents[1] / "contractor_reference.csv"
CACHE = Path("/tmp/state_contractors.json")
STATES_CACHE = Path("/tmp/cms_states.json")
OUTPUT = Path(__file__).resolve().parents[1] / "contractor_states.csv"
BASE_URL = "https://api.coverage.cms.gov/v1"

# state_id -> (name, abbr) — CMS states metadata, including sub-jurisdictions
def fetch_states(token: str) -> dict:
    if STATES_CACHE.exists():
        return json.loads(STATES_CACHE.read_text())
    url = f"{BASE_URL}/metadata/states/"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=30))
    states = {}
    for r in d.get("data", []):
        name = r.get("description", "")
        abbr = ""
        m = re.match(r"^([A-Z][a-z]+)", name)
        if m:
            abbr = m.group(1)[:2].upper()
        states[str(r["state_id"])] = name
    STATES_CACHE.write_text(json.dumps(states, indent=1))
    return states


def get_token() -> str:
    url = f"{BASE_URL}/metadata/license-agreement?ama=true&ada=true&aha=true"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=30))
    return d["data"][0]["Token"]


def fetch_state_contractors(token: str, state_id: int) -> list[str]:
    url = f"{BASE_URL}/reports/local-coverage-articles/?state_id={state_id}&status=A"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=60))
    names = set()
    for row in d.get("data", []):
        raw = (row.get("contractor_name_type") or "").replace("\r", " ").replace("\n", " ")
        name = re.sub(r"\s*\((MAC|HHH|DME)[^)]*\)\s*$", "", raw).strip()
        if name:
            names.add(name)
    return sorted(names)


def main() -> None:
    if not REF_CSV.exists():
        print("ERROR: run scripts/build_contractor_ref.py first")
        sys.exit(1)

    ref: dict[str, dict] = {}
    name_to_ids: dict[str, list[str]] = {}
    for row in csv.DictReader(open(REF_CSV, encoding="utf-8")):
        cid = row["contractor_id"].strip()
        ref[cid] = row
        name_to_ids.setdefault(row["contractor_name"].strip(), []).append(cid)

    token = get_token()
    states = fetch_states(token)

    cache: dict[str, list[str]] = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    for state_id in sorted(states, key=int):
        if state_id in cache:
            continue
        names = fetch_state_contractors(token, int(state_id))
        cache[state_id] = names
        print(f"state {state_id} ({states[state_id]}): {len(names)} contractors")

    CACHE.write_text(json.dumps(cache, indent=1))

    # Join: contractor_name -> states -> contractor_ids
    state_of_contractor: dict[str, set[str]] = {}
    for state_id, names in cache.items():
        for name in names:
            for cid in name_to_ids.get(name, []):
                state_of_contractor.setdefault(cid, set()).add(state_id)

    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["contractor_id", "contractor_name", "states"])
        for cid in sorted(ref, key=int):
            st = sorted(state_of_contractor.get(cid, set()), key=int)
            if st:
                writer.writerow([cid, ref[cid]["contractor_name"], ";".join(st)])

    print(f"Done. {len(state_of_contractor)} contractors with state coverage -> {OUTPUT}")


if __name__ == "__main__":
    main()

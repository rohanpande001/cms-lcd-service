"""
Builds state_reference.csv — state_id, state_name, abbr, state_group.

Sub-jurisdictions are grouped with their parent so that e.g. "NY" resolves to
state 41 (Entire State) plus 63/64/65 (Downstate/Queens/Upstate).

Input: /tmp/cms_states.json (cached CMS states metadata, written by
build_contractor_states.py). Output is static enough to commit.

Run manually:
    python scripts/build_state_reference.py
"""

import csv
import json
import re
import sys
from pathlib import Path

STATES_CACHE = Path("/tmp/cms_states.json")
OUTPUT = Path(__file__).resolve().parents[1] / "state_reference.csv"


# Official USPS abbreviations keyed by CMS state base name (name before " - ")
USPS_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME",
    "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE",
    "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND",
    "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY",
    "American Samoa": "AS", "Guam": "GU", "Northern Mariana Islands": "MP",
    "Puerto Rico": "PR", "Virgin Islands": "VI",
}


GROUPS = {
    "NY": ["41", "63", "64", "65"],
    "CA": ["6", "66", "67"],
    "MO": ["29", "61", "62"],
}


def main() -> None:
    if not STATES_CACHE.exists():
        print(f"ERROR: {STATES_CACHE} not found. Run scripts/build_contractor_states.py first (it fetches state metadata).")
        sys.exit(1)

    states = json.loads(STATES_CACHE.read_text())
    group_of: dict[str, str] = {}
    for group, ids in GROUPS.items():
        for sid in ids:
            group_of[sid] = group

    abbr_of = {k.lower(): v for k, v in USPS_ABBR.items()}

    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["state_id", "state_name", "abbr", "state_group"])
        for sid in sorted(states, key=int):
            name = states[sid]
            base = name.split(" - ")[0]
            abbr = abbr_of.get(base.lower(), "")
            writer.writerow([sid, name, abbr, group_of.get(sid, "")])

    print(f"Done. {len(states)} states -> {OUTPUT}")


if __name__ == "__main__":
    main()

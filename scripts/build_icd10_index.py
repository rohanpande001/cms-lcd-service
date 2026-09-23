"""
Builds the local ICD-10 data files used for J-code → diagnosis resolution:

  icd10_dx_index.csv       (icd10_code, article_id, covered, group)
      - covered=Y rows from /tmp/article_x_icd10_covered.csv
      - covered=N rows from /tmp/article_x_icd10_noncovered.csv
  icd10_descriptions.csv   (icd10_code, description)  — unique codes

Run via:
    make refresh-mapping

Or manually:
    python scripts/build_icd10_index.py
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

csv.field_size_limit(10 * 1024 * 1024)

COVERED_CSV = Path("/tmp/article_x_icd10_covered.csv")
NONCOVERED_CSV = Path("/tmp/article_x_icd10_noncovered.csv")
ROOT = Path(__file__).resolve().parents[1]
OUTPUT_INDEX = ROOT / "icd10_dx_index.csv"
OUTPUT_DESC = ROOT / "icd10_descriptions.csv"


def main() -> None:
    if not COVERED_CSV.exists():
        print(f"ERROR: {COVERED_CSV} not found. Run `make refresh-mapping` to download first.")
        sys.exit(1)

    seen: set[tuple[str, str, str, str]] = set()
    rows: list[list[str]] = []
    descriptions: dict[str, str] = {}

    def add(path: Path, covered: str) -> None:
        group_col = "icd10_covered_group" if covered == "Y" else "icd10_noncovered_group"
        with open(path, encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                aid = row["article_id"].strip().strip('"')
                code = (row.get("icd10_code_id") or "").strip().strip('"').upper()
                group = (row.get(group_col) or "").strip().strip('"')
                desc = (row.get("description") or "").strip().strip('"')
                if not code or not aid:
                    continue
                if desc and code not in descriptions:
                    descriptions[code] = desc
                key = (code, aid, covered, group)
                if key in seen:
                    continue
                seen.add(key)
                rows.append([code, aid, covered, group])

    print("Indexing covered ICD-10 codes...")
    add(COVERED_CSV, "Y")
    if NONCOVERED_CSV.exists():
        print("Indexing non-covered ICD-10 codes...")
        add(NONCOVERED_CSV, "N")
    else:
        print(f"WARNING: {NONCOVERED_CSV} not found — non-covered lists disabled")

    rows.sort()
    with open(OUTPUT_INDEX, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["icd10_code", "article_id", "covered", "group"])
        writer.writerows(rows)
    print(f"  {len(rows):,} index rows -> {OUTPUT_INDEX.name}")

    with open(OUTPUT_DESC, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["icd10_code", "description"])
        for code in sorted(descriptions):
            writer.writerow([code, descriptions[code]])
    print(f"  {len(descriptions):,} unique code descriptions -> {OUTPUT_DESC.name}")


if __name__ == "__main__":
    main()

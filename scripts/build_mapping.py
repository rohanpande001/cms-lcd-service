"""
Rebuilds article_hcpc_mapping.csv from CMS bulk download CSVs.

Run via:
    make refresh-mapping

Or manually:
    python scripts/build_mapping.py
"""

import csv
import sys
from pathlib import Path

csv.field_size_limit(10 * 1024 * 1024)

ARTICLE_CSV = Path("/tmp/article.csv")
HCPC_CSV = Path("/tmp/article_x_hcpc_code.csv")
OUTPUT = Path(__file__).resolve().parents[1] / "article_hcpc_mapping.csv"


def main() -> None:
    for path in (ARTICLE_CSV, HCPC_CSV):
        if not path.exists():
            print(f"ERROR: {path} not found. Run `make refresh-mapping` to download first.")
            sys.exit(1)

    print("Loading article titles...")
    article_titles: dict[str, dict] = {}
    with open(ARTICLE_CSV, encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            aid = row["article_id"].strip().strip('"')
            article_titles[aid] = {
                "title": row["title"].strip().strip('"'),
                "display_id": row.get("display_id", "").strip().strip('"'),
            }

    print("Building HCPC mapping...")
    rows_written = 0
    articles_seen: set[str] = set()

    with open(HCPC_CSV, encoding="utf-8", errors="replace") as fin, \
         open(OUTPUT, "w", newline="", encoding="utf-8") as fout:

        writer = csv.writer(fout)
        writer.writerow([
            "article_id", "display_id", "article_title",
            "hcpc_code", "short_description", "long_description",
        ])

        for row in csv.DictReader(fin):
            aid = row["article_id"].strip().strip('"')
            hcpc = row["hcpc_code_id"].strip().strip('"')
            info = article_titles.get(aid, {"title": "", "display_id": ""})
            writer.writerow([
                aid,
                info["display_id"],
                info["title"],
                hcpc,
                row.get("short_description", "").strip().strip('"'),
                row.get("long_description", "").strip().strip('"'),
            ])
            rows_written += 1
            articles_seen.add(aid)

    print(f"Done. {rows_written:,} rows, {len(articles_seen):,} articles → {OUTPUT}")


if __name__ == "__main__":
    main()

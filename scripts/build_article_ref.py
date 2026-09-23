"""
Builds article_reference.csv — metadata for every article in the CMS bulk download
(current version of each).

The HCPC mapping only contains articles that have J/CPT code links; policy articles
(e.g. 52509, the IVIG policy) have none, so the lookup service needs this reference
to answer article-number searches for any article.

Input: /tmp/article.csv (from current_article_csv.zip). Output is static enough to commit.

Run manually:
    python scripts/build_article_ref.py
"""

import csv
import sys
from pathlib import Path

csv.field_size_limit(10 * 1024 * 1024)

ARTICLE_CSV = Path("/tmp/article.csv")
OUTPUT = Path(__file__).resolve().parents[1] / "article_reference.csv"


def main() -> None:
    if not ARTICLE_CSV.exists():
        print(f"ERROR: {ARTICLE_CSV} not found. Run `make refresh-mapping` to download first.")
        sys.exit(1)

    articles: dict[str, dict] = {}
    with open(ARTICLE_CSV, encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            aid = row["article_id"].strip().strip('"')
            if not aid:
                continue
            # last row wins -> current version
            articles[aid] = {
                "display_id": row.get("display_id", "").strip().strip('"'),
                "article_type": row.get("article_type", "").strip().strip('"'),
                "title": row.get("title", "").strip().strip('"'),
                "status": row.get("status", "").strip().strip('"'),
                "eff_date": row.get("article_eff_date", "").strip().strip('"'),
                "end_date": row.get("article_end_date", "").strip().strip('"'),
            }

    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["article_id", "display_id", "article_type", "title", "status", "article_eff_date", "article_end_date"])
        for aid in sorted(articles, key=int):
            a = articles[aid]
            writer.writerow([aid, a["display_id"], a["article_type"], a["title"], a["status"], a["eff_date"], a["end_date"]])

    print(f"Done. {len(articles):,} articles -> {OUTPUT}")


if __name__ == "__main__":
    main()

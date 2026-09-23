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
CONTRACTOR_X_CSV = Path("/tmp/article_x_contractor.csv")
CONTRACTOR_REF_CSV = Path(__file__).resolve().parents[1] / "contractor_reference.csv"
GOVERNING_CSV = Path(__file__).resolve().parents[1] / "article_governing_map.csv"
OUTPUT = Path(__file__).resolve().parents[1] / "article_hcpc_mapping.csv"


def main() -> None:
    for path in (ARTICLE_CSV, HCPC_CSV):
        if not path.exists():
            print(f"ERROR: {path} not found. Run `make refresh-mapping` to download first.")
            sys.exit(1)

    print("Loading article metadata...")
    article_titles: dict[str, dict] = {}
    with open(ARTICLE_CSV, encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            aid = row["article_id"].strip().strip('"')
            article_titles[aid] = {
                "title": row["title"].strip().strip('"'),
                "display_id": row.get("display_id", "").strip().strip('"'),
                "article_type": row.get("article_type", "").strip().strip('"'),
                "status": row.get("status", "").strip().strip('"'),
                "article_eff_date": row.get("article_eff_date", "").strip().strip('"'),
                "article_end_date": row.get("article_end_date", "").strip().strip('"'),
            }

    # article_id -> set of contractor_ids (current version)
    article_contractors: dict[str, set[str]] = {}
    if CONTRACTOR_X_CSV.exists():
        with open(CONTRACTOR_X_CSV, encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                aid = row["article_id"].strip().strip('"')
                cid = (row.get("contractor_id") or "").strip().strip('"')
                if aid and cid:
                    article_contractors.setdefault(aid, set()).add(cid)
    else:
        print(f"WARNING: {CONTRACTOR_X_CSV} not found — contractor links will be empty")

    contractor_names: dict[str, str] = {}
    if CONTRACTOR_REF_CSV.exists():
        with open(CONTRACTOR_REF_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                contractor_names[row["contractor_id"].strip().strip('"')] = row["contractor_name"].strip().strip('"')

    governing: dict[str, dict] = {}
    if GOVERNING_CSV.exists():
        with open(GOVERNING_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                aid = row["article_id"].strip().strip('"')
                governing[aid] = {
                    "ncd_id": row.get("ncd_id", "").strip().strip('"'),
                    "ncd_section": row.get("ncd_section", "").strip().strip('"'),
                    "ncd_title": row.get("ncd_title", "").strip().strip('"'),
                    "governing_article_id": row.get("governing_article_id", "").strip().strip('"'),
                }
        print(f"Loaded governing map: {len(governing)} articles")
    else:
        print(f"WARNING: {GOVERNING_CSV} not found — ncd_id/governing_article_id will be empty")

    print("Building HCPC mapping...")
    rows_written = 0
    articles_seen: set[str] = set()

    with open(HCPC_CSV, encoding="utf-8", errors="replace") as fin, \
         open(OUTPUT, "w", newline="", encoding="utf-8") as fout:

        writer = csv.writer(fout)
        writer.writerow([
            "article_id", "display_id", "article_type", "article_title",
            "status", "article_eff_date", "article_end_date",
            "ncd_id", "ncd_section", "ncd_title", "governing_article_id",
            "contractor_ids", "contractor_names",
            "hcpc_code", "short_description", "long_description",
        ])

        default_info = {
            "title": "", "display_id": "", "article_type": "", "status": "",
            "article_eff_date": "", "article_end_date": "",
        }
        for row in csv.DictReader(fin):
            aid = row["article_id"].strip().strip('"')
            hcpc = row["hcpc_code_id"].strip().strip('"')
            info = article_titles.get(aid, default_info)
            gov = governing.get(aid, {})
            cids = sorted(article_contractors.get(aid, set()), key=int)
            cnames = sorted({contractor_names[c] for c in cids if c in contractor_names})
            writer.writerow([
                aid,
                info["display_id"],
                info["article_type"],
                info["title"],
                info["status"],
                info["article_eff_date"],
                info["article_end_date"],
                gov.get("ncd_id", ""),
                gov.get("ncd_section", ""),
                gov.get("ncd_title", ""),
                gov.get("governing_article_id", ""),
                ",".join(cids),
                "|".join(cnames),
                hcpc,
                row.get("short_description", "").strip().strip('"'),
                row.get("long_description", "").strip().strip('"'),
            ])
            rows_written += 1
            articles_seen.add(aid)

    print(f"Done. {rows_written:,} rows, {len(articles_seen):,} articles → {OUTPUT}")


if __name__ == "__main__":
    main()

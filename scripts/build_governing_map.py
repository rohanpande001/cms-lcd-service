"""
Builds article_governing_map.csv — for each "Billing and Coding" companion
article, resolves:
  * the governing coverage policy article (type 1/4) whose ICD-10 list is
    fully contained in the companion's list (ICD-10 fingerprint link)
  * the official CMS NCD link (article_related_ncd_documents) with section/title

Run via:
    make refresh-mapping

Or manually (after build_icd10_index.py):
    python scripts/build_governing_map.py

Inputs (from CMS bulk downloads, see Makefile):
    /tmp/article.csv
    /tmp/article_related_ncd_documents.csv
    /tmp/ncd_trkg.csv
    icd10_article_index.csv (project root, built first)
"""

import csv
from collections import defaultdict
from pathlib import Path

csv.field_size_limit(10 * 1024 * 1024)

ARTICLE_CSV = Path("/tmp/article.csv")
NCD_LINK_CSV = Path("/tmp/article_related_ncd_documents.csv")
NCD_TRACKING_CSV = Path("/tmp/ncd_trkg.csv")
INDEX_CSV = Path(__file__).resolve().parents[1] / "icd10_dx_index.csv"
OUTPUT = Path(__file__).resolve().parents[1] / "article_governing_map.csv"

POLICY_TYPES = {"1", "4"}
MIN_POLICY_CODES = 3  # ignore trivial policy lists for fingerprint matching


def clean(value: str) -> str:
    return (value or "").strip().strip('"')


def load_articles() -> dict[str, dict]:
    articles: dict[str, dict] = {}
    with open(ARTICLE_CSV, encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            aid = clean(row["article_id"])
            articles[aid] = {
                "title": clean(row.get("title")),
                "type": clean(row.get("article_type")),
                "status": clean(row.get("status")),
            }
    return articles


def load_icd10_sets() -> dict[str, set[str]]:
    sets: dict[str, set[str]] = defaultdict(set)
    with open(INDEX_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if clean(row.get("covered", "Y")) != "Y":
                continue
            code = clean(row.get("icd10_code")).upper()
            aid = clean(row.get("article_id"))
            if code and aid:
                sets[aid].add(code)
    return sets


def load_ncd_meta() -> dict[str, dict]:
    """Latest NCD version -> (section, title)."""
    best: dict[str, tuple[str, dict]] = {}
    with open(NCD_TRACKING_CSV, encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            ncd_id = clean(row.get("NCD_id"))
            if not ncd_id:
                continue
            try:
                version = int(clean(row.get("NCD_vrsn_num")) or "0")
            except ValueError:
                version = 0
            if ncd_id not in best or version >= best[ncd_id][0]:
                best[ncd_id] = (
                    version,
                    {
                        "section": clean(row.get("NCD_mnl_sect")),
                        "title": clean(row.get("NCD_mnl_sect_title")),
                    },
                )
    return {ncd_id: meta for ncd_id, (_, meta) in best.items()}


def load_ncd_links() -> dict[str, str]:
    """article_id -> latest non-zero related NCD id."""
    best: dict[str, tuple[int, str]] = {}
    with open(NCD_LINK_CSV, encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            aid = clean(row.get("article_id"))
            ncd_id = clean(row.get("r_ncd_id"))
            if not aid or ncd_id == "0":
                continue
            try:
                version = int(clean(row.get("article_version")) or "0")
            except ValueError:
                version = 0
            if aid not in best or version >= best[aid][0]:
                best[aid] = (version, ncd_id)
    return {aid: ncd_id for aid, (_, ncd_id) in best.items()}


def main() -> None:
    for path in (ARTICLE_CSV, NCD_LINK_CSV, NCD_TRACKING_CSV, INDEX_CSV):
        if not path.exists():
            print(f"ERROR: {path} not found. Run `make refresh-mapping` first.")
            return

    articles = load_articles()
    icd10_sets = load_icd10_sets()
    ncd_meta = load_ncd_meta()
    ncd_links = load_ncd_links()

    policies = [
        (aid, codes)
        for aid, info in articles.items()
        if info["status"] == "A"
        and info["type"] in POLICY_TYPES
        and (codes := icd10_sets.get(aid, set()))
        and len(codes) >= MIN_POLICY_CODES
    ]
    print(f"Fingerprint policies: {len(policies)} active type-1/4 articles with >= {MIN_POLICY_CODES} ICD-10 codes")

    rows: list[list[str]] = []
    resolved = 0
    for aid, info in sorted(articles.items()):
        if info["status"] != "A" or aid in {p[0] for p in policies}:
            continue
        ncd_id = ncd_links.get(aid, "")
        meta = ncd_meta.get(ncd_id, {"section": "", "title": ""})

        companion_codes = icd10_sets.get(aid, set())
        governing, governing_title = "", ""
        if companion_codes:
            matches = [
                (p_aid, p_codes)
                for p_aid, p_codes in policies
                if p_codes.issubset(companion_codes)
            ]
            if matches:
                matches.sort(key=lambda m: (len(m[1]), m[0]))  # most specific first
                governing, _ = matches[0]
                governing_title = articles[governing]["title"]
                resolved += 1

        rows.append([
            aid,
            ncd_id,
            meta.get("section", ""),
            meta.get("title", ""),
            governing,
            governing_title,
        ])

    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "article_id", "ncd_id", "ncd_section", "ncd_title",
            "governing_article_id", "governing_title",
        ])
        writer.writerows(rows)

    print(f"Done. {len(rows):,} articles, {resolved:,} resolved to a governing policy article -> {OUTPUT}")


if __name__ == "__main__":
    main()

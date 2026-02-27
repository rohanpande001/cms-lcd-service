import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Path to the mapping CSV — project root
_CSV_PATH = Path(__file__).resolve().parents[2] / "article_hcpc_mapping.csv"

# hcpc_code -> list of article_ids (ordered as they appear in CSV)
_lookup: dict[str, list[str]] = {}


def _load() -> None:
    if not _CSV_PATH.exists():
        logger.warning("article_hcpc_mapping.csv not found at %s — local lookup disabled", _CSV_PATH)
        return

    csv.field_size_limit(10 * 1024 * 1024)
    with open(_CSV_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = row["hcpc_code"].strip()
            article_id = row["article_id"].strip()
            if code not in _lookup:
                _lookup[code] = []
            if article_id not in _lookup[code]:
                _lookup[code].append(article_id)

    logger.info(
        "Local HCPC→article lookup loaded: %d codes across %d unique article IDs (source: %s)",
        len(_lookup),
        len({aid for aids in _lookup.values() for aid in aids}),
        _CSV_PATH.name,
    )


def find_article_ids(hcpc_code: str) -> list[str]:
    """Return all article IDs that govern the given HCPC/CPT code, or []."""
    return _lookup.get(hcpc_code.upper(), [])


# Load once at import time
_load()

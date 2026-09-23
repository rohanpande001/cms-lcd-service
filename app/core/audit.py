"""
Append-only JSONL audit log for article/dx resolution decisions.

Every resolution decision (which article was chosen, why, under which
jurisdiction, and which dx verdict was returned) is persisted to
logs/decisions-YYYYMMDD.jsonl so a denial can be traced back to the exact
evidence used at filing time (RCA item 5: decision persistence).
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


def log_decision(
    *,
    endpoint: str,
    cpt_code: str,
    state: str | None = None,
    contractor: str | None = None,
    icd10: str | None = None,
    article_id: str | None = None,
    selection: str | None = None,
    jurisdiction: str | None = None,
    coverage_source: str | None = None,
    supported: bool | None = None,
    verdict: str | None = None,
) -> None:
    """Write one decision record. Never raises — auditing must not break the request."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "endpoint": endpoint,
        "cpt_code": (cpt_code or "").strip().upper(),
        "state": (state or "").strip() or None,
        "contractor": (contractor or "").strip() or None,
        "icd10": (icd10 or "").strip().upper() or None,
        "article_id": article_id,
        "selection": selection,
        "jurisdiction": jurisdiction,
        "coverage_source": coverage_source,
        "supported": supported,
        "verdict": verdict,
    }
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = _LOG_DIR / f"decisions-{datetime.now(timezone.utc):%Y%m%d}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        logger.exception("Failed to write decision audit log to %s", _LOG_DIR)

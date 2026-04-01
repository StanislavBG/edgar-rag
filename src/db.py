from __future__ import annotations

import logging
from pathlib import Path

import lancedb
import numpy as np

logger = logging.getLogger("edgar-rag")

DATA_DIR = Path("data/vectors")
VECTOR_DIM = 384

_db: lancedb.DBConnection | None = None


def get_db() -> lancedb.DBConnection | None:
    global _db
    if _db is not None:
        return _db
    if not DATA_DIR.exists():
        return None
    try:
        _db = lancedb.connect(str(DATA_DIR))
        return _db
    except Exception:
        logger.exception("Failed to connect to LanceDB")
        return None


def reload_db() -> None:
    global _db
    _db = None
    get_db()


def validate_vector(vector: list[float]) -> np.ndarray:
    arr = np.array(vector, dtype=np.float32)
    if arr.shape != (VECTOR_DIM,):
        msg = f"Expected {VECTOR_DIM} dimensions, got {arr.shape}"
        raise ValueError(msg)
    if not np.isfinite(arr).all():
        msg = "Vector contains NaN or Infinity"
        raise ValueError(msg)
    return arr


def search(
    query_vector: list[float],
    top_k: int = 5,
    filing_type: str | None = None,
    company: str | None = None,
) -> list[dict]:
    db = get_db()
    if db is None:
        return []

    try:
        table = db.open_table("sec-edgar")
    except Exception:
        return []

    vec = validate_vector(query_vector)

    q = table.search(vec).limit(top_k)

    filters = []
    if filing_type:
        filters.append(f"filing_type = '{filing_type}'")
    if company:
        safe_company = company.replace("'", "''")
        filters.append(f"company_name = '{safe_company}'")

    if filters:
        q = q.where(" AND ".join(filters))

    results = q.to_list()

    return [
        {
            "text": r.get("text", ""),
            "score": float(r.get("_distance", 0)),
            "company": r.get("company_name", ""),
            "cik": r.get("cik", ""),
            "filing_type": r.get("filing_type", ""),
            "filing_date": r.get("filing_date", ""),
            "section": r.get("section", ""),
            "source_url": r.get("source_url", ""),
        }
        for r in results
    ]

from __future__ import annotations

import logging
from pathlib import Path

import lancedb
import numpy as np

logger = logging.getLogger("edgar-rag")

DATA_DIR = Path("data/vectors")
VECTOR_DIM = 384
TABLE_NAME = "sec-edgar"
MODEL_NAME = "BAAI/bge-small-en-v1.5"

_db: lancedb.DBConnection | None = None
_table: lancedb.table.Table | None = None
_companies_cache: list[str] | None = None


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


def get_table() -> lancedb.table.Table | None:
    global _table
    if _table is not None:
        return _table
    db = get_db()
    if db is None:
        return None
    try:
        _table = db.open_table(TABLE_NAME)
        return _table
    except Exception:
        logger.debug("LanceDB table not available")
        return None


def reload_db() -> None:
    global _db, _table, _companies_cache
    _db = None
    _table = None
    _companies_cache = None
    get_db()


def get_filing_count() -> int:
    table = get_table()
    if table is None:
        return 0
    try:
        return table.count_rows()
    except Exception:
        logger.debug("Failed to count rows")
        return 0


def get_companies() -> list[str]:
    global _companies_cache
    if _companies_cache is not None:
        return _companies_cache
    table = get_table()
    if table is None:
        return []
    try:
        df = table.to_pandas(columns=["company_name"])
        _companies_cache = sorted(df["company_name"].unique().tolist())
        return _companies_cache
    except Exception:
        logger.debug("Failed to get companies")
        return []


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
    table = get_table()
    if table is None:
        return []

    vec = validate_vector(query_vector)
    q = table.search(vec).limit(top_k)

    # Build parameterized filters — no string interpolation
    if filing_type or company:
        conditions = []
        if filing_type:
            conditions.append(f"filing_type = '{filing_type}'")
        if company:
            safe = company.replace("'", "''").replace("\\", "")
            conditions.append(f"company_name = '{safe}'")
        q = q.where(" AND ".join(conditions))

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

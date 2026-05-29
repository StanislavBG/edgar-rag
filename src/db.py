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
_full_df_cache = None  # materialized full table; only changes on reload_db()


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
    global _db, _table, _companies_cache, _stats_cache, _full_df_cache
    _db = None
    _table = None
    _companies_cache = None
    _stats_cache = None
    _full_df_cache = None
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


def _full_table_df(table):
    """Materialize the full table once and cache it — the data is read-only
    between uploads, so every catalog/stats/company scan can share one copy.
    Invalidated by reload_db(). O(n) on first call, O(1) thereafter.

    (LanceTable.to_pandas() caps at 10 rows — use search().limit() to get all.)
    """
    global _full_df_cache
    if _full_df_cache is not None:
        return _full_df_cache
    count = table.count_rows()
    _full_df_cache = table.search().limit(max(count, 1)).to_pandas()
    return _full_df_cache


def get_indexed_accessions() -> set[str]:
    """Set of accession_numbers already in the vector table — the dedup ledger
    for incremental/scheduled ingests. Empty if the table doesn't exist yet."""
    table = get_table()
    if table is None:
        return set()
    try:
        df = _full_table_df(table)
        return set(df["accession_number"].dropna().unique().tolist())
    except Exception:
        logger.exception("Failed to read indexed accessions")
        return set()


def get_companies() -> list[str]:
    global _companies_cache
    if _companies_cache is not None:
        return _companies_cache
    table = get_table()
    if table is None:
        return []
    try:
        df = _full_table_df(table)
        _companies_cache = sorted(df["company_name"].unique().tolist())
        return _companies_cache
    except Exception:
        logger.exception("Failed to get companies")
        return []


_stats_cache: dict | None = None


def get_data_stats() -> dict:
    """Compute catalog stats once, cache them. Source of truth for all pages."""
    global _stats_cache
    if _stats_cache is not None:
        return _stats_cache
    table = get_table()
    if table is None:
        return {"total_chunks": 0, "total_filings": 0, "companies": [], "date_range": "—"}
    try:
        df = _full_table_df(table)
        # source_url is the canonical unique filing identifier.
        unique_filings = df.drop_duplicates(["source_url"])
        companies = sorted(df["company_name"].unique().tolist())
        dates = df["filing_date"].dropna()
        earliest = dates.min() if len(dates) else ""
        latest = dates.max() if len(dates) else ""
        date_range = (
            f"{earliest[:4]}-{latest[:4]}" if earliest and latest and earliest[:4] != latest[:4]
            else earliest[:4] if earliest else "—"
        )
        _stats_cache = {
            "total_chunks": len(df),
            "total_filings": len(unique_filings),
            "companies": companies,
            "date_range": date_range,
            "earliest": earliest,
            "latest": latest,
        }
        return _stats_cache
    except Exception:
        logger.exception("Failed to compute stats")
        return {"total_chunks": 0, "total_filings": 0, "companies": [], "date_range": "—"}


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


def get_filing_by_accession(accession_number: str) -> list[dict]:
    table = get_table()
    if table is None:
        return []

    safe = accession_number.replace("'", "''").replace("\\", "")[:50]
    try:
        df = (
            table.search()
            .where(f"accession_number = '{safe}'")
            .limit(100)
            .to_pandas()
        )
    except Exception:
        logger.debug("Failed to query filing by accession_number")
        return []

    return [
        {
            "text": r.get("text", "") or "",
            "score": 0.0,
            "company": r.get("company_name", "") or "",
            "cik": r.get("cik", "") or "",
            "filing_type": r.get("filing_type", "") or "",
            "filing_date": r.get("filing_date", "") or "",
            "section": r.get("section", "") or "",
            "source_url": r.get("source_url", "") or "",
            "accession_number": r.get("accession_number", "") or "",
        }
        for r in df.to_dict(orient="records")
    ]

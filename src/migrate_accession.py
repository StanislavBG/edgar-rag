"""One-off migration: fix the accession_number column.

Early ingests derived accession from index_url.split("/")[-2] (the CIK
directory) instead of [-1] (the accession filename), so accession_number held
the CIK. This re-derives the correct accession from source_url in place and
rewrites the table with the canonical SCHEMA. Idempotent.

    python src/migrate_accession.py            # migrate data/vectors
    python src/migrate_accession.py --dry-run  # report only, no write
"""

from __future__ import annotations

import argparse
import logging

import lancedb

from src.db import DATA_DIR, TABLE_NAME, reload_db
from src.ingest import SCHEMA

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("edgar-migrate")


def _accession_from_url(source_url: str) -> str:
    return source_url.rstrip("/").split("/")[-1].replace(".txt", "")


def migrate(dry_run: bool = False) -> int:
    db = lancedb.connect(str(DATA_DIR))
    table = db.open_table(TABLE_NAME)
    df = table.search().limit(max(table.count_rows(), 1)).to_pandas()

    before = df["accession_number"].nunique()
    df["accession_number"] = df["source_url"].map(_accession_from_url)
    # id was f"{accession}_{idx}" — keep it stable by leaving existing ids untouched.
    after = df["accession_number"].nunique()
    logger.info(
        "rows=%d  unique accession before=%d  after=%d  unique source_url=%d",
        len(df),
        before,
        after,
        df["source_url"].nunique(),
    )

    if dry_run:
        logger.info("dry-run — no write")
        return after

    records = df.to_dict(orient="records")
    for r in records:
        r["vector"] = r["vector"].tolist()
    db.create_table(TABLE_NAME, data=records, schema=SCHEMA, mode="overwrite")
    reload_db()
    logger.info("migration complete — table rewritten")
    return after


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix accession_number column")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()

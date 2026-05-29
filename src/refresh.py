"""Local weekly refresh — the cron entry point that keeps prod current.

Runs entirely on the local machine (where torch + ANTHROPIC_API_KEY live):

    1. ingest new filings (idempotent — skips already-indexed accessions)
    2. repair accession_number (safety; cheap no-op once clean)
    3. regenerate intelligence (highlights + metrics) for companies that changed
    4. ship the bundle to the v0.1.0-data GitHub Release (vectors + intelligence)

Prod (Replit) picks up the new bundle on its next redeploy.

    python src/refresh.py                 # full run, ships to the Release
    python src/refresh.py --dry-run       # do everything except ship
    python src/refresh.py --no-intelligence   # data only, skip LLM regen
    python src/refresh.py --since-days 21     # widen the ingest lookback

Suggested crontab (Mondays 06:00 local) — adjust paths:
    0 6 * * 1  cd /home/bilko/Projects/edgar-rag && .venv/bin/python src/refresh.py \
               --companies companies-alpha.txt >> data/refresh.log 2>&1
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess  # nosec B404
from pathlib import Path

from dotenv import load_dotenv

from src.db import _full_table_df, get_indexed_accessions, get_table, reload_db
from src.highlights import COMPANY_NAMES

logger = logging.getLogger("edgar-refresh")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DATA_TAG = "v0.1.0-data"
ASSET_NAME = "edgar-vectors.tar.gz"
SHIP_DIRS = ["vectors", "highlights", "metrics"]  # bundled into the Release asset


def _slug_for_company_name(name: str) -> str | None:
    """Map a table company_name (e.g. 'AMAZON COM INC') to an intelligence slug
    by its leading alphabetic token, ignoring punctuation differences."""
    n = re.sub(r"[^a-z ]", "", name.lower())
    for slug, cname in COMPANY_NAMES.items():
        m = re.match(r"[a-z]+", cname.lower())
        if m and m.group() in n.split():
            return slug
    return None


def _changed_slugs(new_accessions: set[str]) -> set[str]:
    table = get_table()
    if table is None or not new_accessions:
        return set()
    df = _full_table_df(table)
    names = df[df["accession_number"].isin(new_accessions)]["company_name"].unique()
    return {s for s in (_slug_for_company_name(n) for n in names) if s}


def _ship(dry_run: bool) -> None:
    existing = [d for d in SHIP_DIRS if (Path("data") / d).exists()]
    if "vectors" not in existing:
        logger.warning("No data/vectors — nothing to ship")
        return
    tarball = Path("data") / ASSET_NAME
    # shell=False; args as a list (CLAUDE.md security rule)
    subprocess.run(  # nosec B603 B607
        ["tar", "czf", str(tarball), "-C", "data", *existing], check=True
    )
    logger.info("Built %s from %s", tarball, existing)
    if dry_run:
        logger.info("--dry-run — not uploading to the Release")
        return
    subprocess.run(  # nosec B603 B607
        ["gh", "release", "upload", DATA_TAG, str(tarball), "--clobber"], check=True
    )
    logger.info("Uploaded %s to Release %s — redeploy Replit to pick it up", ASSET_NAME, DATA_TAG)


def refresh(
    since_days: int,
    companies_file: str | None,
    with_intelligence: bool,
    dry_run: bool,
) -> None:
    from src.ingest import run_ingest
    from src.migrate_accession import migrate

    load_dotenv()

    reload_db()
    before = get_indexed_accessions()

    logger.info("=== Step 1/4: ingest ===")
    run_ingest(since_days=since_days, companies_file=companies_file)

    logger.info("=== Step 2/4: migrate accession_number ===")
    migrate(dry_run=False)

    reload_db()
    new_accessions = get_indexed_accessions() - before
    logger.info("%d new filings this run", len(new_accessions))

    logger.info("=== Step 3/4: intelligence ===")
    if not new_accessions:
        logger.info("No new filings — skipping intelligence regen")
    elif not with_intelligence:
        logger.info("--no-intelligence — skipping regen")
    elif not os.environ.get("ANTHROPIC_API_KEY"):
        logger.warning("ANTHROPIC_API_KEY not set — skipping intelligence regen")
    else:
        from src.highlights import generate_highlights
        from src.metrics import generate_metrics

        slugs = _changed_slugs(new_accessions)
        logger.info("Regenerating intelligence for: %s", ", ".join(sorted(slugs)) or "(none)")
        for slug in sorted(slugs):
            generate_highlights(slug)
            generate_metrics(slug)

    logger.info("=== Step 4/4: ship ===")
    if not new_accessions and not dry_run:
        logger.info("No new data — skipping ship")
    else:
        _ship(dry_run=dry_run)

    logger.info("Refresh complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local weekly refresh → ship to prod")
    parser.add_argument("--since-days", type=int, default=21)
    parser.add_argument("--companies", type=str, default="companies-alpha.txt")
    parser.add_argument("--no-intelligence", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    refresh(
        since_days=args.since_days,
        companies_file=args.companies,
        with_intelligence=not args.no_intelligence,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()

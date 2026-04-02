"""Ingestion tracker — SQLite database tracking what's been ingested.

Shows coverage matrix: which companies have which filings for which quarters.
Usage:
    python src/tracker.py status              # Show coverage matrix
    python src/tracker.py status --company AAPL  # Show one company
    python src/tracker.py gaps                # Show what's missing
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("data/tracker.db")


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ingested (
            ticker TEXT NOT NULL,
            company_name TEXT NOT NULL,
            cik TEXT NOT NULL,
            filing_type TEXT NOT NULL,
            filing_date TEXT NOT NULL,
            quarter TEXT NOT NULL,
            accession_number TEXT NOT NULL UNIQUE,
            chunks_count INTEGER NOT NULL,
            ingested_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def record_filing(
    ticker: str,
    company_name: str,
    cik: str,
    filing_type: str,
    filing_date: str,
    accession_number: str,
    chunks_count: int,
) -> None:
    """Record a successfully ingested filing."""
    conn = get_conn()
    # Derive quarter from filing_date (YYYY-MM-DD)
    month = int(filing_date.split("-")[1])
    year = filing_date.split("-")[0]
    q = (month - 1) // 3 + 1
    quarter = f"{year}Q{q}"

    conn.execute(
        """
        INSERT OR REPLACE INTO ingested
        (ticker, company_name, cik, filing_type, filing_date, quarter, accession_number,
         chunks_count, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker,
            company_name,
            cik,
            filing_type,
            filing_date,
            quarter,
            accession_number,
            chunks_count,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def get_status(company: str | None = None) -> list[dict]:
    """Get ingestion status, optionally filtered by ticker."""
    conn = get_conn()
    if company:
        rows = conn.execute(
            "SELECT ticker, filing_type, quarter, filing_date, chunks_count, ingested_at "
            "FROM ingested WHERE ticker = ? ORDER BY filing_date DESC",
            (company.upper(),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT ticker, filing_type, quarter, filing_date, chunks_count, ingested_at "
            "FROM ingested ORDER BY ticker, filing_date DESC"
        ).fetchall()
    conn.close()
    return [
        {
            "ticker": r[0],
            "filing_type": r[1],
            "quarter": r[2],
            "filing_date": r[3],
            "chunks": r[4],
            "ingested_at": r[5],
        }
        for r in rows
    ]


def get_coverage_matrix() -> dict[str, dict[str, list[str]]]:
    """Return {ticker: {quarter: [filing_types]}} for coverage view."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT ticker, quarter, filing_type FROM ingested ORDER BY ticker, quarter"
    ).fetchall()
    conn.close()

    matrix: dict[str, dict[str, list[str]]] = {}
    for ticker, quarter, filing_type in rows:
        if ticker not in matrix:
            matrix[ticker] = {}
        if quarter not in matrix[ticker]:
            matrix[ticker][quarter] = []
        matrix[ticker][quarter].append(filing_type)
    return matrix


def get_gaps(companies_file: str = "companies.txt") -> list[dict]:
    """Show companies from companies.txt that have no filings ingested."""
    companies_path = Path(companies_file)
    if not companies_path.exists():
        return []

    tickers = set()
    for line in companies_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            tickers.add(line.upper())

    conn = get_conn()
    ingested_tickers = {
        r[0] for r in conn.execute("SELECT DISTINCT ticker FROM ingested").fetchall()
    }
    conn.close()

    missing = sorted(tickers - ingested_tickers)
    return [{"ticker": t, "status": "not ingested"} for t in missing]


def print_status(company: str | None = None) -> None:
    """Print human-readable status."""
    if company:
        filings = get_status(company)
        if not filings:
            print(f"No filings ingested for {company.upper()}")
            return
        print(f"\n{'Ticker':<8} {'Type':<8} {'Quarter':<10} {'Date':<12} {'Chunks':<8}")
        print("-" * 54)
        for f in filings:
            print(
                f"{f['ticker']:<8} {f['filing_type']:<8} {f['quarter']:<10} "
                f"{f['filing_date']:<12} {f['chunks']:<8}"
            )
    else:
        matrix = get_coverage_matrix()
        if not matrix:
            print("No filings ingested yet.")
            return

        # Collect all quarters
        all_quarters = sorted({q for quarters in matrix.values() for q in quarters}, reverse=True)

        # Print header
        header = f"{'Ticker':<8}"
        for q in all_quarters[:8]:  # Show last 8 quarters
            header += f" {q:<12}"
        print(f"\n{header}")
        print("-" * len(header))

        for ticker in sorted(matrix):
            row = f"{ticker:<8}"
            for q in all_quarters[:8]:
                types = matrix[ticker].get(q, [])
                row += f" {','.join(types) if types else '—':<12}"
            print(row)

    # Summary
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM ingested").fetchone()[0]
    companies = conn.execute("SELECT COUNT(DISTINCT ticker) FROM ingested").fetchone()[0]
    chunks = conn.execute("SELECT SUM(chunks_count) FROM ingested").fetchone()[0] or 0
    conn.close()
    print(f"\nTotal: {total} filings, {companies} companies, {chunks} chunks")


def print_gaps() -> None:
    """Print companies not yet ingested."""
    gaps = get_gaps()
    if not gaps:
        print("All companies from companies.txt have been ingested.")
        return
    print(f"\n{len(gaps)} companies not yet ingested:")
    for g in gaps:
        print(f"  {g['ticker']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion tracker")
    sub = parser.add_subparsers(dest="command")

    status_cmd = sub.add_parser("status", help="Show ingestion status")
    status_cmd.add_argument("--company", type=str, help="Filter by ticker")

    sub.add_parser("gaps", help="Show companies not yet ingested")

    args = parser.parse_args()

    if args.command == "status":
        print_status(args.company)
    elif args.command == "gaps":
        print_gaps()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

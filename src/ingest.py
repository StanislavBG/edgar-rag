"""SEC EDGAR ingestion pipeline.

Downloads, parses, chunks, and embeds SEC filings into LanceDB.
Run locally:
    python src/ingest.py                          # last 35 days, companies.txt
    python src/ingest.py --since-days=90           # last 90 days
    python src/ingest.py --year=2025 --quarter=3   # specific quarter
    python src/ingest.py --companies=my_list.txt   # custom company list
"""

from __future__ import annotations

import argparse
import functools
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import lancedb
import numpy as np
import pyarrow as pa
from dotenv import load_dotenv

from src.db import DATA_DIR, MODEL_NAME, TABLE_NAME, VECTOR_DIM

logger = logging.getLogger("edgar-ingest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

CHUNK_TARGET = 512
CHUNK_MAX = 1024
CHUNK_OVERLAP = 128
SEC_BASE = "https://www.sec.gov"
FILING_TYPES = {"10-K", "10-Q", "8-K"}
REQUEST_DELAY = 0.11  # ~9 req/sec to stay under SEC 10/sec limit


# --- Embedding ---
@functools.lru_cache(maxsize=1)
def _load_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL_NAME)


def embed_texts(texts: list[str]) -> np.ndarray:
    model = _load_model()
    return model.encode(texts, normalize_embeddings=True, show_progress_bar=True, batch_size=256)


# --- Company List ---
COMPANIES_FILE = Path("companies.txt")


def load_companies(path: Path = COMPANIES_FILE) -> set[str]:
    """Load ticker symbols from companies.txt file."""
    if not path.exists():
        logger.warning(f"Companies file not found: {path}. Ingesting ALL filings.")
        return set()
    tickers = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            tickers.add(line.upper())
    logger.info(f"Loaded {len(tickers)} companies from {path}")
    return tickers


def fetch_ticker_to_cik(client: httpx.Client) -> dict[str, str]:
    """Fetch SEC's official ticker-to-CIK mapping."""
    url = f"{SEC_BASE}/files/company_tickers.json"
    time.sleep(REQUEST_DELAY)
    resp = client.get(url)
    if resp.status_code != 200:
        logger.error("Failed to fetch ticker-to-CIK mapping")
        return {}
    data = resp.json()
    mapping = {}
    for entry in data.values():
        ticker = str(entry.get("ticker", "")).upper()
        cik = str(entry.get("cik_str", "")).zfill(10)
        if ticker:
            mapping[ticker] = cik
    logger.info(f"Loaded {len(mapping)} ticker-to-CIK mappings")
    return mapping


# --- SEC EDGAR Download ---
def get_sec_client() -> httpx.Client:
    user_agent = os.environ.get("SEC_USER_AGENT", "")
    if not user_agent:
        logger.error("SEC_USER_AGENT not set. SEC requires a User-Agent header.")
        sys.exit(1)
    return httpx.Client(
        headers={"User-Agent": user_agent},
        timeout=30.0,
        follow_redirects=True,
    )


def fetch_index(
    client: httpx.Client, year: int, quarter: int, allowed_ciks: set[str] | None = None
) -> list[dict]:
    """Fetch the EDGAR full-index for a given quarter and return filing metadata."""
    url = f"{SEC_BASE}/Archives/edgar/full-index/{year}/QTR{quarter}/company.idx"
    logger.info(f"Fetching index: {url}")

    resp = client.get(url)
    if resp.status_code != 200:
        logger.warning(f"Index not found: {url} (status {resp.status_code})")
        return []

    filings = []
    for line in resp.text.splitlines():
        # Index is fixed-width columns separated by 2+ spaces
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 5:
            continue
        company_name = parts[0]
        form_type = parts[1]
        cik = parts[2]
        date_filed = parts[3]
        filename = parts[4]

        if form_type not in FILING_TYPES:
            continue

        padded_cik = cik.zfill(10)
        if allowed_ciks and padded_cik not in allowed_ciks:
            continue

        filings.append(
            {
                "company_name": company_name,
                "filing_type": form_type,
                "cik": cik.zfill(10),
                "filing_date": date_filed,
                "index_url": f"{SEC_BASE}/Archives/{filename}",
            }
        )

    logger.info(f"Found {len(filings)} filings for {year} Q{quarter}")
    return filings


def download_filing(client: httpx.Client, filing: dict) -> str | None:
    """Download a single filing and return its text content.

    The index URL points to the full filing .txt file which contains
    all documents (SEC header + embedded HTML/XBRL). We download it
    directly — it IS the filing.
    """
    time.sleep(REQUEST_DELAY)
    try:
        resp = client.get(filing["index_url"])
        if resp.status_code != 200:
            return None
        return resp.text
    except Exception:
        logger.exception(f"Error downloading {filing['index_url']}")
        return None


# --- Parsing ---
def extract_primary_document(raw: str) -> str:
    """Extract only the primary document (10-K, 10-Q, 8-K HTM) from the raw SEC filing.

    Raw SEC .txt files contain multiple embedded documents (XBRL, exhibits, graphics).
    We only want the first document which is the actual filing HTML.
    """
    lines = raw.splitlines()
    in_primary = False
    primary_lines: list[str] = []

    for line in lines:
        if "<DOCUMENT>" in line and not in_primary:
            in_primary = True
            continue
        if "</DOCUMENT>" in line and in_primary:
            break  # Stop after first document
        if in_primary:
            # Skip the TYPE/SEQUENCE/FILENAME header lines
            if line.startswith("<TYPE>") or line.startswith("<SEQUENCE>"):
                continue
            if line.startswith("<FILENAME>") or line.startswith("<DESCRIPTION>"):
                continue
            primary_lines.append(line)

    return "\n".join(primary_lines)


def parse_filing_text(raw: str) -> str:
    """Extract clean readable text from filing.

    1. Extract primary document (skip XBRL, exhibits, graphics)
    2. Remove inline XBRL tags (ix:nonfraction, ix:nonnumeric, etc.)
    3. Strip HTML tags
    4. Clean up whitespace
    """
    # Step 1: Get only the primary HTM document
    html = extract_primary_document(raw)
    if len(html) < 100:
        # Fallback: maybe it's not in SEC document format
        html = raw

    # Step 2: Remove inline XBRL tags but keep their text content
    html = re.sub(r"<ix:[^>]*>", "", html)
    html = re.sub(r"</ix:[^>]*>", "", html)

    # Step 3: Remove style/script blocks entirely
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)

    # Step 4: Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", html)

    # Step 5: Decode HTML entities
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&[a-zA-Z]+;", " ", text)
    text = re.sub(r"&#\d+;", " ", text)

    # Step 6: Remove XBRL preamble (everything before first real content)
    # Look for common filing start markers
    for marker in [
        "UNITED STATES",
        "SECURITIES AND EXCHANGE",
        "Table of Contents",
        "PART I",
    ]:
        idx = text.find(marker)
        if idx != -1 and idx < len(text) // 2:
            text = text[idx:]
            break

    # Step 7: Clean whitespace (collapse runs of spaces, keep paragraph breaks)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# --- Chunking ---
SECTION_PATTERNS_10K = [
    (r"(?i)\bitem\s+1[.\s]*(?:business)", "Item 1 - Business"),
    (r"(?i)\bitem\s+1a[.\s]*(?:risk\s+factors)", "Item 1A - Risk Factors"),
    (r"(?i)\bitem\s+1b", "Item 1B - Unresolved Staff Comments"),
    (r"(?i)\bitem\s+2[.\s]*(?:properties)", "Item 2 - Properties"),
    (r"(?i)\bitem\s+3[.\s]*(?:legal)", "Item 3 - Legal Proceedings"),
    (r"(?i)\bitem\s+5[.\s]*(?:market)", "Item 5 - Market"),
    (r"(?i)\bitem\s+6[.\s]*(?:reserved|selected)", "Item 6 - Reserved"),
    (r"(?i)\bitem\s+7[.\s]*(?:management|md&a|md\s*&\s*a)", "Item 7 - MD&A"),
    (r"(?i)\bitem\s+7a[.\s]*(?:quantitative|market\s+risk)", "Item 7A - Market Risk"),
    (r"(?i)\bitem\s+8[.\s]*(?:financial\s+statements)", "Item 8 - Financial Statements"),
    (r"(?i)\bitem\s+9[.\s]*(?:changes)", "Item 9 - Changes"),
]


def split_by_sections(text: str, filing_type: str) -> list[tuple[str, str]]:
    """Split text into (section_name, section_text) pairs."""
    if filing_type != "10-K":
        # For 10-Q and 8-K, treat as single section
        return [(f"{filing_type} - Full Text", text)]

    sections = []
    positions = []

    for pattern, name in SECTION_PATTERNS_10K:
        for match in re.finditer(pattern, text):
            positions.append((match.start(), name))

    if not positions:
        return [("10-K - Full Text", text)]

    positions.sort(key=lambda x: x[0])

    for i, (pos, name) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        section_text = text[pos:end].strip()
        if len(section_text) > 50:  # Skip tiny sections
            sections.append((name, section_text))

    return sections if sections else [("10-K - Full Text", text)]


def chunk_text(
    text: str,
    target: int = CHUNK_TARGET,
    max_size: int = CHUNK_MAX,
    overlap: int = CHUNK_OVERLAP,
) -> list[str]:
    """Split text into chunks at paragraph boundaries."""
    words = text.split()
    if len(words) <= target:
        return [text]

    chunks = []
    start = 0
    while start < len(words):
        end = min(start + max_size, len(words))

        # Try to break at a paragraph boundary (double newline or period)
        chunk_words = words[start:end]
        chunk_text_str = " ".join(chunk_words)

        # Look for paragraph break near the target
        if end < len(words) and len(chunk_words) > target:
            # Find last period near target
            target_end = start + target
            best_break = target_end
            for j in range(target_end, min(target_end + 100, end)):
                if j < len(words) and words[j - 1].endswith("."):
                    best_break = j
                    break
            end = best_break
            chunk_text_str = " ".join(words[start:end])

        chunks.append(chunk_text_str)
        start = end - overlap if end < len(words) else end

    return chunks


def create_chunks(filing: dict, text: str) -> list[dict]:
    """Create chunks from a single filing."""
    sections = split_by_sections(text, filing["filing_type"])
    # Accession is the filename in the index URL (…/<cik>/<accession>.txt), NOT the
    # parent dir (which is the CIK). [-2] was the long-standing data bug.
    accession = filing.get(
        "accession_number", filing["index_url"].split("/")[-1].replace(".txt", "")
    )
    source_url = filing["index_url"]

    all_chunks = []
    chunk_idx = 0

    for section_name, section_text in sections:
        text_chunks = chunk_text(section_text)
        for chunk in text_chunks:
            if len(chunk.split()) < 10:  # Skip very short chunks
                continue
            all_chunks.append(
                {
                    "id": f"{accession}_{chunk_idx}",
                    "text": chunk,
                    "company_name": filing["company_name"],
                    "cik": filing["cik"],
                    "filing_type": filing["filing_type"],
                    "filing_date": filing["filing_date"],
                    "section": section_name,
                    "accession_number": accession,
                    "source_url": source_url,
                }
            )
            chunk_idx += 1

    return all_chunks


# --- LanceDB Storage ---
SCHEMA = pa.schema(
    [
        pa.field("id", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), VECTOR_DIM)),
        pa.field("text", pa.string()),
        pa.field("company_name", pa.string()),
        pa.field("cik", pa.string()),
        pa.field("filing_type", pa.string()),
        pa.field("filing_date", pa.string()),
        pa.field("section", pa.string()),
        pa.field("accession_number", pa.string()),
        pa.field("source_url", pa.string()),
    ]
)


def store_chunks(chunks: list[dict], vectors: np.ndarray) -> int:
    """Store chunks with vectors in LanceDB. Returns count of rows stored."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(DATA_DIR))

    records = []
    for chunk, vec in zip(chunks, vectors, strict=True):
        records.append({**chunk, "vector": vec.tolist()})

    try:
        table = db.open_table(TABLE_NAME)
        table.add(records)
    except Exception:
        db.create_table(TABLE_NAME, data=records, schema=SCHEMA)

    table = db.open_table(TABLE_NAME)
    return table.count_rows()


# --- Main Pipeline ---
def get_quarters_for_days(since_days: int) -> list[tuple[int, int]]:
    """Return (year, quarter) pairs covering the given number of days."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=since_days)

    quarters = []
    current = start
    while current <= now:
        q = (current.month - 1) // 3 + 1
        pair = (current.year, q)
        if pair not in quarters:
            quarters.append(pair)
        current += timedelta(days=91)

    # Always include current quarter
    current_q = (now.month - 1) // 3 + 1
    current_pair = (now.year, current_q)
    if current_pair not in quarters:
        quarters.append(current_pair)

    return quarters


def run_ingest(
    since_days: int = 35,
    year: int | None = None,
    quarter: int | None = None,
    companies_file: str | None = None,
) -> None:
    load_dotenv()

    if year and quarter:
        quarters = [(year, quarter)]
    else:
        quarters = get_quarters_for_days(since_days)

    logger.info(f"Ingesting quarters: {quarters}")

    client = get_sec_client()

    # Load company filter
    companies_path = Path(companies_file) if companies_file else COMPANIES_FILE
    tickers = load_companies(companies_path)
    allowed_ciks: set[str] | None = None
    # Build CIK mappings
    ticker_to_cik: dict[str, str] = {}
    cik_to_ticker: dict[str, str] = {}
    if tickers:
        ticker_to_cik = fetch_ticker_to_cik(client)
        allowed_ciks = set()
        for ticker in tickers:
            cik = ticker_to_cik.get(ticker)
            if cik:
                allowed_ciks.add(cik)
                cik_to_ticker[cik] = ticker
            else:
                logger.warning(f"Ticker not found in SEC mapping: {ticker}")
        logger.info(f"Filtering to {len(allowed_ciks)} companies by CIK")

    all_chunks: list[dict] = []
    filing_stats: list[dict] = []  # For tracker

    for y, q in quarters:
        filings = fetch_index(client, y, q, allowed_ciks=allowed_ciks)
        logger.info(f"Processing {len(filings)} filings for {y} Q{q}")

        for i, filing in enumerate(filings):
            if (i + 1) % 50 == 0:
                logger.info(f"  Progress: {i + 1}/{len(filings)}")

            html = download_filing(client, filing)
            if not html:
                continue

            text = parse_filing_text(html)
            if len(text) < 100:
                continue

            chunks = create_chunks(filing, text)
            all_chunks.extend(chunks)

            # Track this filing
            ticker = cik_to_ticker.get(filing["cik"], filing["cik"])
            accession = filing.get(
                "accession_number", filing["index_url"].split("/")[-1].replace(".txt", "")
            )
            filing_stats.append(
                {
                    "ticker": ticker,
                    "company_name": filing["company_name"],
                    "cik": filing["cik"],
                    "filing_type": filing["filing_type"],
                    "filing_date": filing["filing_date"],
                    "accession_number": accession,
                    "chunks_count": len(chunks),
                }
            )

    client.close()

    if not all_chunks:
        logger.warning("No chunks generated. Check if filings were downloaded successfully.")
        return

    logger.info(f"Embedding {len(all_chunks)} chunks...")
    texts = [c["text"] for c in all_chunks]
    vectors = embed_texts(texts)

    logger.info("Storing in LanceDB...")
    total = store_chunks(all_chunks, vectors)
    logger.info(f"Done. Total rows in LanceDB: {total}")

    from src.tracker import record_filings_batch

    record_filings_batch(filing_stats)
    logger.info(f"Tracked {len(filing_stats)} filings in tracker.db")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest SEC EDGAR filings")
    parser.add_argument(
        "--since-days", type=int, default=35, help="Days to look back (default: 35)"
    )
    parser.add_argument("--year", type=int, help="Specific year")
    parser.add_argument("--quarter", type=int, choices=[1, 2, 3, 4], help="Specific quarter")
    parser.add_argument(
        "--companies", type=str, help="Path to companies file (default: companies.txt)"
    )
    args = parser.parse_args()

    run_ingest(
        since_days=args.since_days,
        year=args.year,
        quarter=args.quarter,
        companies_file=args.companies,
    )


if __name__ == "__main__":
    main()

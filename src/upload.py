"""Upload local vector data to Replit.

Run after ingestion to push vectors:
    python src/upload.py

Splits large uploads into 25MB chunks to bypass Cloud Run limits.
"""

from __future__ import annotations

import os
import sys
import tarfile
import tempfile
from pathlib import Path

import httpx
from dotenv import load_dotenv

from src.db import DATA_DIR

MAX_CHUNK_MB = 10


def main() -> None:
    load_dotenv()

    replit_url = os.environ.get("REPLIT_URL", "").rstrip("/")
    upload_secret = os.environ.get("UPLOAD_SECRET", "")

    if not replit_url:
        print("Error: REPLIT_URL not set in .env")
        sys.exit(1)
    if not upload_secret:
        print("Error: UPLOAD_SECRET not set in .env")
        sys.exit(1)
    if not DATA_DIR.exists():
        print(f"Error: {DATA_DIR} does not exist. Run ingestion first.")
        sys.exit(1)

    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = tmp.name

    print(f"Compressing {DATA_DIR}...")
    with tarfile.open(tmp_path, "w:gz") as tar:
        tar.add(str(DATA_DIR), arcname="vectors")

    size_mb = Path(tmp_path).stat().st_size / (1024 * 1024)
    print(f"Compressed: {size_mb:.1f} MB")

    if size_mb <= MAX_CHUNK_MB:
        _upload_file(replit_url, upload_secret, tmp_path)
    else:
        _upload_chunked(replit_url, upload_secret, tmp_path, size_mb)

    Path(tmp_path).unlink(missing_ok=True)


def _upload_file(replit_url: str, secret: str, path: str) -> None:
    size_mb = Path(path).stat().st_size / (1024 * 1024)
    print(f"Uploading {size_mb:.1f} MB to {replit_url}/upload-vectors...")
    with open(path, "rb") as f:
        response = httpx.post(
            f"{replit_url}/upload-vectors",
            headers={"Authorization": f"Bearer {secret}"},
            files={"file": ("vectors.tar.gz", f, "application/gzip")},
            timeout=600.0,
        )
    if response.status_code == 200:
        data = response.json()
        print(f"Upload successful. Filings count: {data.get('filings_count', 'unknown')}")
    else:
        print(f"Upload failed: {response.status_code} {response.text[:200]}")
        sys.exit(1)


def _upload_chunked(replit_url: str, secret: str, path: str, total_mb: float) -> None:
    """Split tar.gz into chunks and upload sequentially via /upload-vectors-chunk."""
    chunk_size = MAX_CHUNK_MB * 1024 * 1024
    file_size = Path(path).stat().st_size
    chunks = (file_size + chunk_size - 1) // chunk_size

    print(f"Splitting {total_mb:.1f} MB into {chunks} chunks (max {MAX_CHUNK_MB} MB each)...")

    with open(path, "rb") as f:
        for i in range(chunks):
            chunk_data = f.read(chunk_size)
            is_last = i == chunks - 1

            print(f"  Uploading chunk {i + 1}/{chunks} ({len(chunk_data) / 1024 / 1024:.1f} MB)...")

            response = httpx.post(
                f"{replit_url}/upload-vectors-chunk",
                headers={
                    "Authorization": f"Bearer {secret}",
                    "X-Chunk-Index": str(i),
                    "X-Chunk-Total": str(chunks),
                    "X-Chunk-Final": "true" if is_last else "false",
                },
                files={"file": (f"chunk_{i}.bin", chunk_data, "application/octet-stream")},
                timeout=600.0,
            )

            if response.status_code != 200:
                print(f"  Chunk {i + 1} failed: {response.status_code} {response.text[:200]}")
                sys.exit(1)

            if is_last:
                data = response.json()
                print(f"Upload complete. Filings count: {data.get('filings_count', 'unknown')}")


if __name__ == "__main__":
    main()

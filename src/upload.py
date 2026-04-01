"""Upload local vector data to Replit.

Run after ingestion to push vectors:
    python src/upload.py
"""

from __future__ import annotations

import os
import sys
import tarfile
import tempfile
from pathlib import Path

import httpx
from dotenv import load_dotenv

DATA_DIR = Path("data/vectors")


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
    print(f"Uploading {size_mb:.1f} MB to {replit_url}/upload-vectors...")

    with open(tmp_path, "rb") as f:
        response = httpx.post(
            f"{replit_url}/upload-vectors",
            headers={"Authorization": f"Bearer {upload_secret}"},
            files={"file": ("vectors.tar.gz", f, "application/gzip")},
            timeout=600.0,
        )

    Path(tmp_path).unlink(missing_ok=True)

    if response.status_code == 200:
        data = response.json()
        print(f"Upload successful. Filings count: {data.get('filings_count', 'unknown')}")
    else:
        print(f"Upload failed: {response.status_code} {response.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()

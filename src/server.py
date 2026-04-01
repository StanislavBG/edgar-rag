from __future__ import annotations

import json
import logging
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Request, UploadFile
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.db import get_db, reload_db
from src.mcp import router as mcp_router
from src.query import router as query_router

logger = logging.getLogger("edgar-rag")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DATA_DIR = Path("data/vectors")

app = FastAPI(title="EDGAR RAG", version="0.1.0")
app.include_router(query_router)
app.include_router(mcp_router)

# --- Rate limiter ---
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. 60 requests per minute."},
    )


# --- Middleware ---
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "edgar-rag.bilko.run,localhost,127.0.0.1").split(
    ","
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def request_size_limit(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        max_size = 2 * 1024 * 1024 * 1024 if request.url.path == "/upload-vectors" else 50 * 1024
        if int(content_length) > max_size:
            return JSONResponse(status_code=413, content={"detail": "Request body too large"})
    return await call_next(request)


# --- Auth dependency ---
def verify_upload_token(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = os.environ.get("UPLOAD_SECRET", "")
    if not authorization or not expected:
        raise _unauthorized()
    if authorization != f"Bearer {expected}":
        raise _unauthorized()


def _unauthorized() -> Exception:
    from fastapi import HTTPException

    raise HTTPException(status_code=401, detail="Unauthorized")


# --- Endpoints ---
@app.get("/health")
async def health() -> dict:
    db = get_db()
    filing_count = 0
    if db is not None:
        try:
            table = db.open_table("sec-edgar")
            filing_count = table.count_rows()
        except Exception:
            logger.debug("LanceDB table not available")
    return {
        "status": "ok",
        "filings_count": filing_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/upload-vectors")
async def upload_vectors(
    file: UploadFile,
    _auth: Annotated[None, Depends(verify_upload_token)],
) -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "vectors.tar.gz"
        with open(tmp_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

        extract_dir = Path(tmpdir) / "extracted"
        extract_dir.mkdir()
        with tarfile.open(tmp_path, "r:gz") as tar:
            tar.extractall(extract_dir, filter="data")

        extracted_vectors = extract_dir / "vectors"
        if not extracted_vectors.exists():
            for item in extract_dir.iterdir():
                if item.is_dir():
                    extracted_vectors = item
                    break

        DATA_DIR.parent.mkdir(parents=True, exist_ok=True)
        if DATA_DIR.exists():
            shutil.rmtree(DATA_DIR)
        shutil.move(str(extracted_vectors), str(DATA_DIR))

    reload_db()

    filing_count = 0
    db = get_db()
    if db is not None:
        try:
            table = db.open_table("sec-edgar")
            filing_count = table.count_rows()
        except Exception:
            logger.debug("LanceDB table not available")

    logger.info(
        json.dumps(
            {
                "event": "vectors_uploaded",
                "filing_count": filing_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
    )

    return {"status": "ok", "filings_count": filing_count}

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

from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.db import DATA_DIR, get_companies, get_filing_count, reload_db
from src.mcp import TOOLS
from src.mcp import router as mcp_router
from src.query import QueryRequest
from src.query import router as query_router

logger = logging.getLogger("edgar-rag")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="EDGAR RAG", version="0.1.0")
app.include_router(query_router)
app.include_router(mcp_router)


# --- x402 Payment Middleware ---
def _setup_x402() -> None:
    wallet = os.environ.get("WALLET_ADDRESS", "")
    facilitator_url = os.environ.get("X402_FACILITATOR_URL", "https://x402.org/facilitator")

    if not wallet:
        logger.warning("WALLET_ADDRESS not set — x402 payment gating DISABLED")
        return

    try:
        from x402.http import (
            FacilitatorConfig,
            HTTPFacilitatorClient,
            PaymentOption,
            RouteConfig,
        )
        from x402.http.middleware.fastapi import PaymentMiddlewareASGI
        from x402.mechanisms.evm.exact import ExactEvmServerScheme
        from x402.server import x402ResourceServer

        facilitator = HTTPFacilitatorClient(FacilitatorConfig(url=facilitator_url))
        server = x402ResourceServer(facilitator)
        server.register("eip155:8453", ExactEvmServerScheme())

        routes = {
            "POST /v1/query": RouteConfig(
                accepts=PaymentOption(
                    scheme="exact",
                    price="$0.01",
                    network="eip155:8453",
                    pay_to=wallet,
                ),
                description="Search SEC EDGAR filings",
            ),
        }

        app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)
        logger.info("x402 payment gating ENABLED on /v1/query ($0.01 USDC on Base)")

    except Exception:
        logger.exception("Failed to initialize x402 — payment gating DISABLED")


_setup_x402()

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. 60 requests per minute."},
    )


ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")
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


def verify_upload_token(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = os.environ.get("UPLOAD_SECRET", "")
    if not authorization or not expected:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@app.get("/")
async def root(request: Request) -> dict:
    filing_count = get_filing_count()
    companies = get_companies()
    base = _base_url(request)
    tool_schema = QueryRequest.model_json_schema()

    return {
        "service": "EDGAR RAG",
        "version": "0.1.0",
        "tagline": "SEC filings for AI agents. $0.01 per query. No account needed.",
        "description": (
            "Semantic search over SEC EDGAR filings (10-K, 10-Q, 8-K). "
            "Send a natural language query, get back relevant passages with "
            "company name, filing date, section label, and source URL. "
            "Pay per query via the x402 micropayment protocol — no API keys, "
            "no subscriptions, no accounts. Your agent pays $0.01 in USDC "
            "on Base L2 and gets instant results."
        ),
        "status": {
            "filings_indexed": filing_count,
            "companies_available": companies,
            "filing_types": ["10-K", "10-Q", "8-K"],
            "data_source": "SEC EDGAR (public domain)",
            "update_frequency": "Monthly",
        },
        "quick_start": {
            "step_1": f"Send a POST to {base}/v1/query with your search query",
            "step_2": "Receive HTTP 402 with payment details (price, wallet, network)",
            "step_3": "Your x402 SDK signs a $0.01 USDC payment on Base L2",
            "step_4": "Retry the same request with the payment proof header",
            "step_5": "Receive HTTP 200 with ranked filing passages and citations",
        },
        "endpoints": {
            "POST /v1/query": {
                "description": "Search SEC filings by natural language query",
                "cost": "$0.01 USDC per request (via x402)",
                "request_schema": tool_schema,
                "response_fields": {
                    "results[]": {
                        "text": "Matched passage from the filing",
                        "score": "Similarity score (lower = more relevant)",
                        "company": "Company name",
                        "cik": "SEC Central Index Key",
                        "filing_type": "10-K, 10-Q, or 8-K",
                        "filing_date": "Date filed (ISO format)",
                        "section": "Filing section (e.g. Item 1A - Risk Factors)",
                        "source_url": "Direct link to filing on SEC EDGAR",
                    }
                },
                "example_requests": [
                    {
                        "description": "Search for revenue data",
                        "curl": (
                            f"curl -X POST {base}/v1/query "
                            '-H "Content-Type: application/json" '
                            '-d \'{"query": "total revenue and net income", "top_k": 3}\''
                        ),
                    },
                    {
                        "description": "iPhone sales by region",
                        "curl": (
                            f"curl -X POST {base}/v1/query "
                            '-H "Content-Type: application/json" '
                            '-d \'{"query": "iPhone sales by region",'
                            ' "company": "Apple Inc.", "filing_type": "10-Q"}\''
                        ),
                    },
                    {
                        "description": "Risk factors",
                        "curl": (
                            f"curl -X POST {base}/v1/query "
                            '-H "Content-Type: application/json" '
                            '-d \'{"query": "main risk factors",'
                            ' "filing_type": "10-Q", "top_k": 2}\''
                        ),
                    },
                ],
                "error_responses": {
                    "402": "Payment required — send x402 payment and retry",
                    "413": "Request body exceeds 50KB",
                    "422": "Validation error — invalid fields or extra fields",
                    "429": "Rate limit exceeded (60/min)",
                    "503": "Payment verification unavailable (fail-closed)",
                },
            },
            "POST /mcp": {
                "description": "MCP Streamable HTTP endpoint (JSON-RPC 2.0)",
                "free_methods": ["initialize", "tools/list"],
                "paid_methods": {"tools/call": "$0.01 USDC via x402"},
                "tool_schemas": TOOLS,
            },
            "GET /health": {
                "description": "Service health check (free)",
                "example_response": {
                    "status": "ok",
                    "filings_count": filing_count,
                },
            },
        },
        "payment": {
            "protocol": "x402 (HTTP 402 Payment Required)",
            "how_it_works": (
                "Hit a paid endpoint without payment -> get 402 with price details -> "
                "your x402 SDK pays $0.01 USDC on Base L2 -> retry with proof -> get results. "
                "~200ms overhead. No accounts needed."
            ),
            "price": "$0.01 per query",
            "asset": "USDC on Base L2 (chain ID 8453)",
            "sdk": {
                "python": "pip install x402",
                "typescript": "npm install @x402/fetch",
                "docs": "https://docs.x402.org/",
            },
        },
        "why_use_this": {
            "for_agents": (
                "Fresh SEC filing data without re-embedding terabytes yourself. "
                "$0.01 per lookup vs ~$500/month to build your own pipeline."
            ),
            "for_developers": (
                "Semantic search across 10-K, 10-Q, and 8-K filings "
                "with section labels and source citations. No pipeline to manage."
            ),
        },
        "legal": {
            "terms_of_service": {
                "acceptance": ("By sending requests to this API, you agree to these terms."),
                "service": (
                    "EDGAR RAG provides semantic search over publicly available "
                    "SEC EDGAR filings. Results are passages from public filings, "
                    "not financial advice."
                ),
                "no_warranty": (
                    "This service is provided 'as is' without warranty. "
                    "Filing data may be incomplete, delayed, or contain parsing artifacts. "
                    "Always verify against the original SEC filing via the source_url."
                ),
                "rate_limits": "60 requests per minute per IP address.",
                "acceptable_use": (
                    "Automated queries from AI agents and software are welcome. "
                    "Do not use this service for market manipulation, fraud, "
                    "or any illegal activity."
                ),
                "liability": (
                    "We are not liable for trading losses, investment decisions, "
                    "or any damages arising from use of this service or its data."
                ),
                "changes": "Terms may be updated. Continued use constitutes acceptance.",
            },
            "data_privacy": {
                "what_we_collect": (
                    "We log: client IP address, payment transaction hashes, "
                    "request timestamps, and response status codes."
                ),
                "what_we_do_not_collect": (
                    "We do NOT log query content, payment headers, "
                    "wallet addresses, or any personally identifiable information."
                ),
                "data_source": (
                    "All filing data comes from SEC EDGAR, which is public domain "
                    "(U.S. government work, no copyright). We do not scrape, "
                    "crawl, or collect data from private sources."
                ),
                "payments": (
                    "Payments are processed on-chain via the x402 protocol on Base L2. "
                    "We receive USDC at our wallet address. We do not store your "
                    "wallet private keys or payment credentials."
                ),
                "retention": (
                    "Server logs are retained for up to 30 days for operational "
                    "monitoring and abuse detection, then deleted."
                ),
                "contact": "bilko@bilko.run",
            },
        },
        "source_code": "https://github.com/StanislavBG/edgar-rag",
    }


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "filings_count": get_filing_count(),
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
    filing_count = get_filing_count()

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

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


# --- x402 Payment Middleware ---
def _setup_x402() -> None:
    """Configure x402 payment gating on /v1/query. Skipped if wallet not configured."""
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
        server.register("eip155:8453", ExactEvmServerScheme())  # Base mainnet

        routes = {
            "POST /v1/query": RouteConfig(
                accepts=PaymentOption(
                    scheme="exact",
                    price="$0.01",
                    network="eip155:8453",  # Base L2
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
@app.get("/")
async def root() -> dict:
    """Landing page — comprehensive API docs for agents and humans."""
    db = get_db()
    filing_count = 0
    companies: list[str] = []
    if db is not None:
        try:
            table = db.open_table("sec-edgar")
            filing_count = table.count_rows()
            df = table.to_pandas()
            companies = sorted(df["company_name"].unique().tolist())
        except Exception:
            logger.debug("LanceDB table not available")

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
            "step_1": "Send a POST to /v1/query with your search query",
            "step_2": "Receive HTTP 402 with payment details (price, wallet, network)",
            "step_3": "Your x402 SDK signs a $0.01 USDC payment on Base L2",
            "step_4": "Retry the same request with the payment proof header",
            "step_5": "Receive HTTP 200 with ranked filing passages and citations",
        },
        "endpoints": {
            "POST /v1/query": {
                "description": "Search SEC filings by natural language query",
                "cost": "$0.01 USDC per request (via x402)",
                "request": {
                    "content_type": "application/json",
                    "body": {
                        "query": {
                            "type": "string",
                            "required": True,
                            "max_length": 1000,
                            "description": "Natural language search query",
                        },
                        "filing_type": {
                            "type": "string",
                            "required": False,
                            "enum": ["10-K", "10-Q", "8-K"],
                            "description": "Filter results to a specific filing type",
                        },
                        "company": {
                            "type": "string",
                            "required": False,
                            "max_length": 200,
                            "description": "Filter results to a specific company name",
                        },
                        "top_k": {
                            "type": "integer",
                            "required": False,
                            "default": 5,
                            "min": 1,
                            "max": 20,
                            "description": "Number of results to return",
                        },
                    },
                },
                "response_fields": {
                    "results": [
                        {
                            "text": "The matched passage from the filing",
                            "score": "Similarity score (lower = more relevant)",
                            "company": "Company name (e.g. Apple Inc.)",
                            "cik": "SEC Central Index Key",
                            "filing_type": "10-K, 10-Q, or 8-K",
                            "filing_date": "Date filed with SEC (ISO format)",
                            "section": "Filing section (e.g. Item 1A - Risk Factors)",
                            "source_url": "Direct link to the filing on SEC EDGAR",
                        }
                    ]
                },
                "example_requests": [
                    {
                        "description": "Search for revenue data",
                        "curl": (
                            "curl -X POST https://edgar-rag.replit.app/v1/query "
                            '-H "Content-Type: application/json" '
                            '-d \'{"query": "What was total revenue and net income?",'
                            ' "top_k": 3}\''
                        ),
                        "body": {
                            "query": "What was total revenue and net income?",
                            "top_k": 3,
                        },
                    },
                    {
                        "description": "Search for iPhone sales by region",
                        "curl": (
                            "curl -X POST https://edgar-rag.replit.app/v1/query "
                            '-H "Content-Type: application/json" '
                            '-d \'{"query": "iPhone sales performance by region",'
                            ' "company": "Apple Inc.", "filing_type": "10-Q"}\''
                        ),
                        "body": {
                            "query": "iPhone sales performance by region",
                            "company": "Apple Inc.",
                            "filing_type": "10-Q",
                        },
                    },
                    {
                        "description": "Search for risk factors",
                        "curl": (
                            "curl -X POST https://edgar-rag.replit.app/v1/query "
                            '-H "Content-Type: application/json" '
                            '-d \'{"query": "What are the main risk factors?",'
                            ' "filing_type": "10-Q", "top_k": 2}\''
                        ),
                        "body": {
                            "query": "What are the main risk factors?",
                            "filing_type": "10-Q",
                            "top_k": 2,
                        },
                    },
                    {
                        "description": "Search for gross margin trends",
                        "curl": (
                            "curl -X POST https://edgar-rag.replit.app/v1/query "
                            '-H "Content-Type: application/json" '
                            '-d \'{"query": "gross margin trends and profitability"}\''
                        ),
                        "body": {
                            "query": "gross margin trends and profitability",
                        },
                    },
                ],
                "error_responses": {
                    "402": {
                        "description": "Payment required — x402 payment not provided",
                        "headers": {
                            "X-Payment-Required": (
                                "Base64-encoded JSON with payment details: "
                                "amount, asset (USDC), network (Base L2), "
                                "payTo (wallet address), facilitator URL"
                            ),
                        },
                    },
                    "413": {"description": "Request body exceeds 50KB limit"},
                    "422": {
                        "description": "Validation error — invalid field values",
                        "example": {
                            "detail": [
                                {
                                    "loc": ["body", "filing_type"],
                                    "msg": "Input should be '10-K', '10-Q' or '8-K'",
                                }
                            ]
                        },
                    },
                    "429": {"description": "Rate limit exceeded (60 requests per minute)"},
                    "503": {
                        "description": (
                            "Payment verification unavailable — "
                            "service will NOT serve results for free (fail-closed)"
                        )
                    },
                },
            },
            "POST /mcp": {
                "description": "MCP (Model Context Protocol) Streamable HTTP endpoint",
                "transport": "JSON-RPC 2.0 over HTTP POST",
                "methods": {
                    "initialize": {
                        "cost": "free",
                        "description": "Returns server info and capabilities",
                        "example_request": {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                        },
                    },
                    "tools/list": {
                        "cost": "free",
                        "description": "Returns available tool schemas",
                        "example_request": {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/list",
                        },
                    },
                    "tools/call": {
                        "cost": "$0.01 USDC via x402",
                        "description": "Execute a tool (search_filings)",
                        "example_request": {
                            "jsonrpc": "2.0",
                            "id": 3,
                            "method": "tools/call",
                            "params": {
                                "name": "search_filings",
                                "arguments": {
                                    "query": "Apple gross margin trends",
                                    "top_k": 2,
                                },
                            },
                        },
                    },
                },
                "tool_schemas": [
                    {
                        "name": "search_filings",
                        "description": (
                            "Search SEC EDGAR filings by semantic query. "
                            "Returns relevant text passages with citations."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "required": ["query"],
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "maxLength": 1000,
                                },
                                "filing_type": {
                                    "type": "string",
                                    "enum": ["10-K", "10-Q", "8-K"],
                                },
                                "company": {
                                    "type": "string",
                                    "maxLength": 200,
                                },
                                "top_k": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 20,
                                    "default": 5,
                                },
                            },
                        },
                    }
                ],
            },
            "GET /health": {
                "description": "Service health check",
                "cost": "free",
                "example_response": {
                    "status": "ok",
                    "filings_count": filing_count,
                    "timestamp": "2026-04-01T00:00:00Z",
                },
            },
        },
        "payment": {
            "protocol": "x402 (HTTP 402 Payment Required)",
            "how_it_works": (
                "The x402 protocol enables instant, frictionless micropayments over HTTP. "
                "When you hit a paid endpoint without payment, you get a 402 response "
                "with machine-readable payment details. Your x402-compatible SDK or agent "
                "automatically signs a USDC transfer on Base L2 and retries the request "
                "with the payment proof. The entire flow adds ~200ms latency."
            ),
            "price_per_query": "$0.01 USD",
            "asset": "USDC (USD Coin — stablecoin, 1 USDC = $1.00)",
            "network": "Base L2 (Coinbase's Ethereum Layer 2, chain ID 8453)",
            "transaction_cost": "~$0.0001 per payment on Base L2",
            "facilitator": "https://x402.org/facilitator (Coinbase, free tier)",
            "sdk_links": {
                "python": "pip install x402",
                "typescript": "npm install @x402/fetch",
                "specification": "https://docs.x402.org/",
            },
        },
        "why_use_this": {
            "for_agents": (
                "Your AI agent needs fresh SEC filing data but re-embedding "
                "terabytes of EDGAR filings costs thousands in compute. "
                "Pay $0.01 per lookup instead. No accounts, no API keys — "
                "just HTTP + x402."
            ),
            "for_developers": (
                "Build financial research tools, compliance checkers, or "
                "trading signals without managing an EDGAR pipeline. "
                "Semantic search finds relevant passages across 10-K, 10-Q, "
                "and 8-K filings with section labels and source citations."
            ),
            "vs_alternatives": {
                "vs_building_yourself": (
                    "Downloading, parsing, chunking, and embedding SEC filings "
                    "costs ~$500/month in compute and weeks of engineering. "
                    "We charge $0.01 per query."
                ),
                "vs_valyu": (
                    "Valyu charges $8 per 1,000 tokens for financial data. "
                    "We charge $0.01 per query regardless of result size."
                ),
            },
        },
        "source_code": "https://github.com/StanislavBG/edgar-rag",
        "contact": "bilko@bilko.run",
    }


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

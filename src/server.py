from __future__ import annotations

import json
import logging
import os
import shutil
import tarfile
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.audit import AuditMiddleware
from src.audit import router as admin_router
from src.company import router as company_router
from src.db import DATA_DIR, get_companies, get_data_stats, get_filing_count, reload_db
from src.errors import ErrorCode, make_error
from src.mcp import TOOLS
from src.mcp import router as mcp_router
from src.query import QueryRequest
from src.query import router as query_router

logger = logging.getLogger("edgar-rag")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Query price — alpha is free ($0.00), set X402_PRICE env var to charge
QUERY_PRICE = os.environ.get("X402_PRICE", "$0.00")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Pre-load embedding model on startup so first request is fast."""
    from src.query import embed_query

    logger.info("Warming up embedding model...")
    embed_query("warmup")
    logger.info("Embedding model ready")
    yield


app = FastAPI(title="EDGAR RAG", version="0.1.0", lifespan=lifespan)
app.include_router(query_router)
app.include_router(mcp_router)
app.include_router(company_router)
app.include_router(admin_router)
app.add_middleware(AuditMiddleware)


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
                    price=QUERY_PRICE,
                    network="eip155:8453",
                    pay_to=wallet,
                ),
                description="Search SEC EDGAR filings",
            ),
        }

        app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)
        logger.info(f"x402 payment gating ENABLED on /v1/query ({QUERY_PRICE} USDC on Base)")

    except Exception:
        logger.exception("Failed to initialize x402 — payment gating DISABLED")


_setup_x402()


@app.middleware("http")
async def admin_bypass(request: Request, call_next):
    """Allow admin requests to bypass x402 payment for testing."""
    admin_key = request.headers.get("x-admin-key", "")
    expected = os.environ.get("UPLOAD_SECRET", "")
    if admin_key and expected and admin_key == expected:
        # Strip the x402 payment requirement by marking as pre-paid
        request.state.x402_bypass = True
    return await call_next(request)


limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return make_error(
        code=ErrorCode.RATE_LIMIT_EXCEEDED,
        message="Rate limit exceeded. 60 requests per minute.",
        retry=True,
        status_code=429,
    )


def _map_validation_error(err: dict) -> tuple[ErrorCode, str, str | None]:
    """Map a single Pydantic validation error dict to (code, message, field)."""
    loc = err.get("loc", ())
    # Skip the first element if it's the body/path/header marker (not "query" — that's a field name)
    field_parts = [str(p) for p in loc if p not in ("body", "path", "header", "cookie")]
    field = field_parts[-1] if field_parts else None
    err_type = err.get("type", "")
    msg = err.get("msg", "")

    if err_type in ("extra_forbidden",) or "extra" in err_type or "forbidden" in err_type:
        return (
            ErrorCode.UNKNOWN_FIELDS,
            f"Unknown field not allowed: {field}" if field else "Unknown fields not allowed",
            field,
        )
    if field == "filing_type" and err_type == "literal_error":
        return (
            ErrorCode.INVALID_FILING_TYPE,
            "filing_type must be one of: 10-K, 10-Q, 8-K",
            field,
        )
    if field == "query" and ("at most 1000" in msg or err_type == "string_too_long"):
        return (
            ErrorCode.QUERY_TOO_LONG,
            "query must be at most 1000 characters",
            field,
        )
    if field == "query" and ("at least 1" in msg or err_type == "string_too_short"):
        return (
            ErrorCode.QUERY_EMPTY,
            "query must not be empty",
            field,
        )
    if field == "top_k":
        return (
            ErrorCode.TOP_K_OUT_OF_RANGE,
            "top_k must be between 1 and 20",
            field,
        )
    return (ErrorCode.INTERNAL_ERROR, msg or "Validation error", field)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    if errors:
        code, message, field = _map_validation_error(errors[0])
    else:
        code, message, field = ErrorCode.INTERNAL_ERROR, "Validation error", None
    return make_error(
        code=code,
        message=message,
        retry=False,
        status_code=422,
        field=field,
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
        upload_paths = {"/upload-vectors", "/upload-vectors-chunk"}
        max_size = 2 * 1024 * 1024 * 1024 if request.url.path in upload_paths else 50 * 1024
        if int(content_length) > max_size:
            return make_error(
                code=ErrorCode.REQUEST_TOO_LARGE,
                message="Request body too large",
                retry=False,
                status_code=413,
            )
    return await call_next(request)


def verify_upload_token(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = os.environ.get("UPLOAD_SECRET", "")
    if not authorization or not expected:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _build_api_data(request: Request) -> dict:
    filing_count = get_filing_count()
    companies = get_companies()
    base = _base_url(request)
    tool_schema = QueryRequest.model_json_schema()

    return {
        "service": "EDGAR RAG",
        "version": "0.1.0",
        "tagline": f"SEC filings for AI agents. {QUERY_PRICE} per query (free during alpha). No account needed.",
        "description": (
            "Semantic search over SEC EDGAR filings (10-K, 10-Q, 8-K). "
            "Send a natural language query, get back relevant passages with "
            "company name, filing date, section label, and source URL. "
            "Pay per query via the x402 micropayment protocol — no API keys, "
            f"no subscriptions, no accounts. Your agent pays {QUERY_PRICE} in USDC "
            "on Base L2 and gets instant results. (Free during alpha.)"
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
            "step_3": f"Your x402 SDK signs a {QUERY_PRICE} USDC payment on Base L2",
            "step_4": "Retry the same request with the payment proof header",
            "step_5": "Receive HTTP 200 with ranked filing passages and citations",
        },
        "endpoints": {
            "POST /v1/query": {
                "description": "Search SEC filings by natural language query",
                "cost": f"{QUERY_PRICE} USDC per request (via x402)",
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
                "paid_methods": {"tools/call": f"{QUERY_PRICE} USDC via x402"},
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
                f"your x402 SDK pays {QUERY_PRICE} USDC on Base L2 -> retry with proof -> get results. "
                "~200ms overhead. No accounts needed."
            ),
            "price": f"{QUERY_PRICE} per query (free during alpha)",
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
                f"{QUERY_PRICE} per lookup (free during alpha) vs ~$500/month to build your own pipeline."
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


@app.get("/")
async def root(request: Request):
    """Serve HTML to browsers, JSON to agents."""
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        from src.landing import render_landing

        stats = get_data_stats()
        filing_count = stats.get("total_chunks", 0)
        companies = stats.get("companies", [])
        total_filings = stats.get("total_filings", 0)
        date_range = stats.get("date_range", "—")
        base = _base_url(request)
        tool_schema = QueryRequest.model_json_schema()
        admin_key = request.headers.get("x-admin-key", "")
        expected = os.environ.get("UPLOAD_SECRET", "")
        is_admin = bool(admin_key and expected and admin_key == expected)
        html = render_landing(
            base,
            filing_count,
            companies,
            tool_schema,
            is_admin=is_admin,
            price=QUERY_PRICE,
            total_filings=total_filings,
            date_range=date_range,
        )
        return HTMLResponse(content=html)
    return _build_api_data(request)


@app.get("/api")
async def api_docs(request: Request) -> dict:
    """Machine-readable JSON API documentation."""
    return _build_api_data(request)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "filings_count": get_filing_count(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/data")
async def data_catalog(request: Request):
    """Show exactly what data is indexed — for agents and humans."""
    from src.db import _full_table_df, get_table

    table = get_table()
    catalog: dict = {"status": "empty", "companies": []}

    if table is not None:
        try:
            df = _full_table_df(table)[
                ["company_name", "cik", "filing_type", "filing_date", "section"]
            ]
            companies_data = []
            for company_name in sorted(df["company_name"].unique()):
                company_df = df[df["company_name"] == company_name]
                cik = company_df["cik"].iloc[0]

                filings = []
                for _, row in (
                    company_df.groupby(["filing_type", "filing_date"])
                    .size()
                    .reset_index(name="chunks")
                    .iterrows()
                ):
                    filings.append(
                        {
                            "type": row["filing_type"],
                            "date": row["filing_date"],
                            "chunks": int(row["chunks"]),
                        }
                    )
                filings.sort(key=lambda x: x["date"], reverse=True)

                sections = sorted(company_df["section"].unique().tolist())
                filing_types = sorted(company_df["filing_type"].unique().tolist())
                date_range = {
                    "earliest": company_df["filing_date"].min(),
                    "latest": company_df["filing_date"].max(),
                }

                companies_data.append(
                    {
                        "name": company_name,
                        "cik": cik,
                        "total_chunks": len(company_df),
                        "filing_types": filing_types,
                        "date_range": date_range,
                        "sections": sections,
                        "filings": filings,
                    }
                )

            catalog = {
                "status": "available",
                "total_chunks": len(df),
                "total_companies": len(companies_data),
                "companies": companies_data,
            }
        except Exception:
            logger.exception("Failed to build data catalog")

    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return HTMLResponse(content=_render_data_page(request, catalog))
    return catalog


def _render_data_page(request: Request, catalog: dict) -> str:
    base = _base_url(request)
    total = catalog.get("total_chunks", 0)
    company_count = catalog.get("total_companies", 0)
    is_alpha_free = QUERY_PRICE in ("$0.00", "$0", "0", "$0.0")
    price_short = "Free" if is_alpha_free else QUERY_PRICE

    companies_html = ""
    for c in catalog.get("companies", []):
        filings_rows = ""
        for f in c["filings"]:
            filings_rows += (
                f"<tr><td>{f['type']}</td><td>{f['date']}</td><td>{f['chunks']} passages</td></tr>"
            )

        sections_badges = " ".join(f'<span class="badge">{s}</span>' for s in c.get("sections", []))

        companies_html += f"""
        <div class="company-card">
            <h3>{c["name"]} <span style="color: var(--muted); font-weight: 400;">({c["cik"]})</span></h3>
            <p>{c["total_chunks"]} passages | {len(c["filings"])} filings | {c["date_range"]["earliest"]} to {c["date_range"]["latest"]}</p>
            <p style="font-size: 0.85rem; color: var(--muted);">Filing types: {", ".join(c["filing_types"])}</p>
            <div style="margin: 0.5rem 0;">{sections_badges}</div>
            <details>
                <summary style="cursor: pointer; color: var(--accent); font-size: 0.85rem;">View all filings</summary>
                <table style="margin-top: 0.5rem;">
                    <tr><th>Type</th><th>Filed</th><th>Data</th></tr>
                    {filings_rows}
                </table>
            </details>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Data Catalog — EDGAR RAG</title>
    <meta name="description" content="Complete catalog of SEC filings indexed by EDGAR RAG. {company_count} companies, {total} passages from 10-K, 10-Q, and 8-K filings.">
    <style>
        :root {{ --bg: #0a0a0a; --surface: #141414; --border: #262626; --text: #e5e5e5; --muted: #a3a3a3; --accent: #3b82f6; --green: #22c55e; }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); line-height: 1.7; }}
        .container {{ max-width: 900px; margin: 0 auto; padding: 2rem 1.5rem; }}
        h1 {{ font-size: 2rem; font-weight: 700; margin-bottom: 0.5rem; }}
        h2 {{ font-size: 1.3rem; font-weight: 600; margin: 2rem 0 1rem; color: var(--accent); }}
        h3 {{ font-size: 1.1rem; font-weight: 600; margin: 0 0 0.25rem; }}
        p {{ margin-bottom: 0.5rem; color: var(--muted); }}
        a {{ color: var(--accent); text-decoration: none; }}
        .badge {{ display: inline-block; padding: 0.15rem 0.5rem; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; font-size: 0.7rem; margin: 0.1rem; color: var(--muted); }}
        .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 0.75rem; margin: 1.5rem 0; }}
        .stat {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 1rem; }}
        .stat-value {{ font-size: 1.5rem; font-weight: 700; }}
        .stat-label {{ font-size: 0.8rem; color: var(--muted); }}
        .company-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; margin: 1rem 0; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
        th, td {{ text-align: left; padding: 0.5rem; border-bottom: 1px solid var(--border); }}
        th {{ color: var(--muted); font-weight: 500; }}
        details {{ margin-top: 0.5rem; }}
        .nav {{ margin-bottom: 1.5rem; font-size: 0.85rem; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="nav"><a href="{base}/">EDGAR RAG</a> / <a href="{base}/data">Data Catalog</a></div>
        <h1>Data Catalog</h1>
        <p style="color: var(--text);">Complete inventory of what's indexed and searchable. Every filing listed below is available via the <a href="{base}/api">/v1/query API</a> and <a href="{base}/api">MCP endpoint</a>.</p>

        <div class="stat-grid">
            <div class="stat"><div class="stat-value">{total:,}</div><div class="stat-label">Total passages</div></div>
            <div class="stat"><div class="stat-value">{company_count}</div><div class="stat-label">Companies</div></div>
            <div class="stat"><div class="stat-value">{price_short}</div><div class="stat-label">Per query</div></div>
        </div>

        <h2>Indexed Companies</h2>
        {companies_html}

        <div style="margin-top: 3rem; padding-top: 1.5rem; border-top: 1px solid var(--border); font-size: 0.85rem; color: var(--muted);">
            <p>Data sourced from <a href="https://www.sec.gov/edgar/searchedgar/companysearch">SEC EDGAR</a> (public domain). Updated monthly.</p>
            <p><a href="{base}/">Back to EDGAR RAG</a> | <a href="{base}/api">API (JSON)</a> | <a href="mailto:bilko@bilko.run">Request a company</a></p>
        </div>
    </div>
</body>
</html>"""


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


# Chunked upload state
_chunk_dir: Path | None = None


@app.post("/upload-vectors-chunk")
async def upload_vectors_chunk(
    file: UploadFile,
    _auth: Annotated[None, Depends(verify_upload_token)],
    x_chunk_index: Annotated[str, Header()],
    x_chunk_total: Annotated[str, Header()],
    x_chunk_final: Annotated[str, Header()],
) -> dict:
    """Receive chunked uploads for large vector datasets."""
    global _chunk_dir
    if _chunk_dir is None or not _chunk_dir.exists():
        _chunk_dir = Path(tempfile.mkdtemp(prefix="edgar-chunks-"))

    chunk_path = _chunk_dir / f"chunk_{x_chunk_index}"
    with open(chunk_path, "wb") as f:
        while data := await file.read(1024 * 1024):
            f.write(data)

    if x_chunk_final != "true":
        return {"status": "ok", "chunk": int(x_chunk_index), "received": True}

    # Reassemble and extract
    total = int(x_chunk_total)
    assembled = _chunk_dir / "vectors.tar.gz"
    with open(assembled, "wb") as out:
        for i in range(total):
            cp = _chunk_dir / f"chunk_{i}"
            with open(cp, "rb") as inp:
                out.write(inp.read())

    extract_dir = _chunk_dir / "extracted"
    extract_dir.mkdir()
    with tarfile.open(str(assembled), "r:gz") as tar:
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

    shutil.rmtree(_chunk_dir, ignore_errors=True)
    _chunk_dir = None

    reload_db()
    filing_count = get_filing_count()

    logger.info(
        json.dumps(
            {
                "event": "vectors_uploaded_chunked",
                "filing_count": filing_count,
                "chunks_received": total,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
    )

    return {"status": "ok", "filings_count": filing_count}

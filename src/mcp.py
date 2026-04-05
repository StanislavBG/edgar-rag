"""MCP Streamable HTTP endpoint.

Handles MCP JSON-RPC 2.0 over HTTP.
Free: initialize, tools/list, list_companies, get_data_catalog, get_filing
Paid: tools/call search_filings (price via X402_PRICE env var)
"""

from __future__ import annotations

import logging
import os
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.db import (
    get_companies,
    get_filing_by_accession,
    get_filing_count,
    get_table,
    search,
)
from src.query import QueryRequest, embed_query

logger = logging.getLogger("edgar-rag")

router = APIRouter()

QUERY_PRICE = os.environ.get("X402_PRICE", "$0.00")

SERVER_INFO = {
    "name": "edgar-rag",
    "version": "0.1.0",
    "description": (
        "Semantic search over SEC EDGAR filings (10-K, 10-Q, 8-K). "
        "Query by natural language, get ranked passages with citations. "
        f"{QUERY_PRICE}/query via x402 (free during alpha). "
        "Free tools: list_companies, get_data_catalog, get_filing."
    ),
    "capabilities": {"tools": {}},
}

_schema = QueryRequest.model_json_schema()
_props = _schema.get("properties", {})

TOOLS = [
    {
        "name": "search_filings",
        "description": (
            "Search SEC EDGAR filings by semantic query. Returns relevant text "
            "passages with company, filing type, date, section, and source URL. "
            f"Costs {QUERY_PRICE} USDC via x402 (free during alpha)."
        ),
        "inputSchema": {
            "type": "object",
            "required": _schema.get("required", ["query"]),
            "properties": {
                k: {kk: vv for kk, vv in v.items() if kk != "title"}
                for k, v in _props.items()
            },
        },
    },
    {
        "name": "list_companies",
        "description": (
            "List all companies with indexed SEC filings. Free — no payment required. "
            "Returns company names, filing counts, and date ranges."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_filing",
        "description": (
            "Retrieve all passages from a specific SEC filing by its accession "
            "number. Free — no payment required."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["accession_number"],
            "properties": {
                "accession_number": {
                    "type": "string",
                    "maxLength": 50,
                    "description": (
                        "SEC accession number, e.g. 0000320193-25-000073"
                    ),
                },
            },
        },
    },
    {
        "name": "get_data_catalog",
        "description": (
            "Get full catalog of indexed data — companies, filing types, date ranges, "
            "passage counts. Free — no payment required."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _jsonrpc_response(req_id: int | str | None, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: int | str | None, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _build_catalog() -> dict:
    """Build data catalog for MCP tool response."""
    table = get_table()
    if table is None:
        return {"total_chunks": 0, "companies": []}

    try:
        df = table.to_pandas(columns=["company_name", "cik", "filing_type", "filing_date"])
        companies = []
        for name in sorted(df["company_name"].unique()):
            cdf = df[df["company_name"] == name]
            companies.append({
                "name": name,
                "cik": cdf["cik"].iloc[0],
                "chunks": len(cdf),
                "filing_types": sorted(cdf["filing_type"].unique().tolist()),
                "date_range": f"{cdf['filing_date'].min()} to {cdf['filing_date'].max()}",
                "filings": len(cdf.groupby(["filing_type", "filing_date"])),
            })
        return {"total_chunks": len(df), "companies": companies}
    except Exception:
        return {"total_chunks": get_filing_count(), "companies": []}


@router.post("/mcp")
async def mcp_handler(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            content=_jsonrpc_error(None, -32700, "Parse error"),
            status_code=400,
        )

    method = body.get("method", "")
    req_id = body.get("id")
    params = body.get("params", {})

    if method == "initialize":
        return JSONResponse(content=_jsonrpc_response(req_id, SERVER_INFO))

    if method == "tools/list":
        return JSONResponse(content=_jsonrpc_response(req_id, {"tools": TOOLS}))

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        # Free tools
        if tool_name == "list_companies":
            companies = get_companies()
            catalog = _build_catalog()
            text = f"Available companies ({len(companies)}):\n\n"
            for c in catalog.get("companies", []):
                text += (
                    f"- {c['name']} (CIK: {c['cik']}): "
                    f"{c['chunks']} passages, {c['filings']} filings, "
                    f"{', '.join(c['filing_types'])}, {c['date_range']}\n"
                )
            return JSONResponse(
                content=_jsonrpc_response(
                    req_id, {"content": [{"type": "text", "text": text}]}
                )
            )

        if tool_name == "get_data_catalog":
            catalog = _build_catalog()
            text = "EDGAR RAG Data Catalog\n"
            text += f"Total indexed: {catalog['total_chunks']} passages\n\n"
            for c in catalog.get("companies", []):
                text += (
                    f"{c['name']} ({c['cik']})\n"
                    f"  Filings: {c['filings']} ({', '.join(c['filing_types'])})\n"
                    f"  Passages: {c['chunks']}\n"
                    f"  Coverage: {c['date_range']}\n\n"
                )
            return JSONResponse(
                content=_jsonrpc_response(
                    req_id, {"content": [{"type": "text", "text": text}]}
                )
            )

        if tool_name == "get_filing":
            accession_number = arguments.get("accession_number", "")
            if not isinstance(accession_number, str) or len(accession_number) > 50:
                return JSONResponse(
                    content=_jsonrpc_error(
                        req_id, -32602, "Invalid params: accession_number"
                    ),
                    status_code=400,
                )
            if not re.fullmatch(r"[A-Za-z0-9-]{1,50}", accession_number):
                return JSONResponse(
                    content=_jsonrpc_error(
                        req_id,
                        -32602,
                        "Invalid params: accession_number must be alphanumeric + dashes",
                    ),
                    status_code=400,
                )

            passages = get_filing_by_accession(accession_number)
            if not passages:
                text = f"No passages found for accession number: {accession_number}\n"
                return JSONResponse(
                    content=_jsonrpc_response(
                        req_id, {"content": [{"type": "text", "text": text}]}
                    )
                )

            first = passages[0]
            header = (
                f"Filing: {first['company']} {first['filing_type']} "
                f"({first['filing_date']})\n"
                f"Source: {first['source_url']}\n\n"
                f"Passages ({len(passages)}):\n"
            )
            lines = [header]
            for i, p in enumerate(passages, 1):
                section = p.get("section", "") or ""
                snippet = (p.get("text", "") or "")[:300]
                lines.append(f"{i}. [{section}] {snippet}...")
            text = "\n".join(lines)

            return JSONResponse(
                content=_jsonrpc_response(
                    req_id, {"content": [{"type": "text", "text": text}]}
                )
            )

        # Paid tool
        if tool_name == "search_filings":
            try:
                req = QueryRequest(**arguments)
            except Exception as e:
                return JSONResponse(
                    content=_jsonrpc_error(req_id, -32602, f"Invalid params: {e}"),
                    status_code=400,
                )

            query_vector = embed_query(req.query)
            results = search(
                query_vector=query_vector,
                top_k=req.top_k or 5,
                filing_type=req.filing_type,
                company=req.company,
            )

            text_parts = [f"Found {len(results)} relevant passages:\n"]
            for i, r in enumerate(results, 1):
                text_parts.append(
                    f"{i}. [{r['company']}, {r['filing_type']}, "
                    f"{r['filing_date']}, {r['section']}]\n"
                    f'"{r["text"][:500]}"\n'
                    f"Source: {r['source_url']}\n"
                    f"Score: {r['score']:.2f}\n"
                )

            return JSONResponse(
                content=_jsonrpc_response(
                    req_id,
                    {"content": [{"type": "text", "text": "\n".join(text_parts)}]},
                )
            )

        return JSONResponse(
            content=_jsonrpc_error(req_id, -32601, f"Unknown tool: {tool_name}"),
            status_code=400,
        )

    return JSONResponse(
        content=_jsonrpc_error(req_id, -32601, f"Method not found: {method}"),
        status_code=400,
    )

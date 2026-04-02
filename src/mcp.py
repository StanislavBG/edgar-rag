"""MCP Streamable HTTP endpoint.

Handles MCP JSON-RPC 2.0 over HTTP.
- initialize, tools/list: free
- tools/call: x402-gated (handled in server.py)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.db import search
from src.query import QueryRequest, embed_query

logger = logging.getLogger("edgar-rag")

router = APIRouter()

SERVER_INFO = {
    "name": "edgar-rag",
    "version": "0.1.0",
    "capabilities": {"tools": {}},
}

# Derive tool schema from the Pydantic model — single source of truth
_schema = QueryRequest.model_json_schema()
_props = _schema.get("properties", {})

TOOLS = [
    {
        "name": "search_filings",
        "description": (
            "Search SEC EDGAR filings (10-K, 10-Q, 8-K) by semantic query."
            " Returns relevant text passages with citations."
        ),
        "inputSchema": {
            "type": "object",
            "required": _schema.get("required", ["query"]),
            "properties": {
                k: {kk: vv for kk, vv in v.items() if kk != "title"} for k, v in _props.items()
            },
        },
    }
]


def _jsonrpc_response(req_id: int | str | None, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: int | str | None, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


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

        if tool_name != "search_filings":
            return JSONResponse(
                content=_jsonrpc_error(req_id, -32601, f"Unknown tool: {tool_name}"),
                status_code=400,
            )

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

        text_parts = [f"Found {len(results)} relevant passages from SEC EDGAR filings:\n"]
        for i, r in enumerate(results, 1):
            text_parts.append(
                f"{i}. [{r['company']}, {r['filing_type']}, {r['filing_date']}, {r['section']}]\n"
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
        content=_jsonrpc_error(req_id, -32601, f"Method not found: {method}"),
        status_code=400,
    )

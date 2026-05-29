"""Core unit + validation tests.

Kept model-free: every case here either exercises pure logic or hits a path
where FastAPI rejects the request (422/401/413) before any embedding runs, so
the suite stays fast and needs no ONNX/sbert model or vector data.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src import server
from src.errors import ErrorCode
from src.migrate_accession import _accession_from_url


# --- accession migration helper ---
@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.sec.gov/Archives/edgar/data/1018724/0001018724-24-000083.txt",
         "0001018724-24-000083"),
        ("https://x/y/0000320193-25-000073.txt", "0000320193-25-000073"),
        ("https://x/y/0000320193-25-000073", "0000320193-25-000073"),
        ("https://x/y/acc.txt/", "acc"),
    ],
)
def test_accession_from_url(url, expected):
    assert _accession_from_url(url) == expected


# --- constant-time secret comparison ---
def test_secure_eq():
    assert server._secure_eq("abc", "abc") is True
    assert server._secure_eq("abc", "abd") is False
    assert server._secure_eq("", "abc") is False
    assert server._secure_eq("abc", "") is False
    assert server._secure_eq("", "") is False


# --- price gating ---
@pytest.mark.parametrize("price,free", [
    ("$0.00", True), ("$0", True), ("0", True), ("", True),
    ("$0.01", False), ("$1.00", False),
])
def test_price_is_free(price, free):
    assert server._price_is_free(price) is free


# --- validation-error mapping (pure) ---
def test_map_validation_error_unknown_field():
    code, _, field = server._map_validation_error(
        {"loc": ("body", "bogus"), "type": "extra_forbidden", "msg": ""}
    )
    assert code == ErrorCode.UNKNOWN_FIELDS
    assert field == "bogus"


def test_map_validation_error_filing_type():
    code, _, _ = server._map_validation_error(
        {"loc": ("body", "filing_type"), "type": "literal_error", "msg": ""}
    )
    assert code == ErrorCode.INVALID_FILING_TYPE


# --- request validation through the app (no model load) ---
@pytest.fixture
def client():
    # No `with` block → app lifespan (model warmup) does not run.
    return TestClient(server.app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_query_rejects_extra_fields(client):
    r = client.post("/v1/query", json={"query": "hi", "bogus": 1})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == ErrorCode.UNKNOWN_FIELDS.value


def test_query_rejects_bad_filing_type(client):
    r = client.post("/v1/query", json={"query": "hi", "filing_type": "10-Z"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == ErrorCode.INVALID_FILING_TYPE.value


def test_query_rejects_empty_query(client):
    r = client.post("/v1/query", json={"query": ""})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == ErrorCode.QUERY_EMPTY.value


def test_query_rejects_topk_out_of_range(client):
    r = client.post("/v1/query", json={"query": "hi", "top_k": 99})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == ErrorCode.TOP_K_OUT_OF_RANGE.value


def test_upload_requires_auth(client):
    r = client.post("/upload-vectors")
    assert r.status_code in (401, 422)  # 401 token, or 422 missing file


def test_admin_requires_key(client):
    r = client.get("/admin/api/stats")
    assert r.status_code == 401


# --- intelligence MCP tools ---
def _call(client, name, args):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": name, "arguments": args}},
    )


def test_mcp_tools_list_includes_intelligence(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"get_company_highlights", "get_company_metrics"} <= names


def test_slug_resolution_by_ticker_and_name():
    from src.mcp import _resolve_slug
    assert _resolve_slug("AAPL") == "apple"
    assert _resolve_slug("apple") == "apple"
    assert _resolve_slug("Apple Inc.") == "apple"
    assert _resolve_slug("Nonesuch") is None


def test_metrics_unknown_company_errors(client):
    j = _call(client, "get_company_metrics", {"company": "Nonesuch"}).json()
    assert j["error"]["code"] == -32602


def test_metrics_no_data_is_graceful(client):
    # A tracked company with no generated metrics yet returns text, not an error.
    j = _call(client, "get_company_metrics", {"company": "nvidia"}).json()
    assert "result" in j and "No metrics" in j["result"]["content"][0]["text"]

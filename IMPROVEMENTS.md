# EDGAR RAG Improvements — Implementation Plan

Five independent improvements to implement. Each section is self-contained and suitable for a junior developer.

---

## Task A: Host Embedding Model on GitHub Releases

**Goal:** Eliminate HuggingFace dependency at runtime. Currently, every Replit deploy re-downloads the 127MB ONNX model from HuggingFace, which is slow and a supply chain risk.

**Why:** HuggingFace can be slow, rate-limited, or temporarily unavailable. The model doesn't change often — we can pin a specific version in our own GitHub Release.

**Steps:**
1. Download the ONNX model files locally to `models/bge-small-en-v1.5/`:
   - `onnx/model.onnx` (127MB)
   - `tokenizer.json`
   - `tokenizer_config.json`
   - `config.json`
   - `special_tokens_map.json`
2. Create a tar.gz: `cd models && tar czf bge-small-en-v1.5.tar.gz bge-small-en-v1.5/`
3. Create a GitHub Release `v0.1.0-model` on `StanislavBG/edgar-rag` and upload the tar.gz as an asset
4. Update `install.sh` to download from GitHub Releases URL instead of HuggingFace Hub
5. Update `src/query.py` `_load_model()` to load from local path (`./models/bge-small-en-v1.5/onnx/model.onnx`) first, fall back to HuggingFace Hub if not present
6. Add env var `MODEL_CACHE_DIR` (default: `./models`) for the model location

**Files to modify:**
- `install.sh` — add `curl -L -o models.tar.gz https://github.com/StanislavBG/edgar-rag/releases/download/v0.1.0-model/bge-small-en-v1.5.tar.gz && tar xzf models.tar.gz -C . && rm models.tar.gz`
- `src/query.py` — `_load_model()` checks `Path("models/bge-small-en-v1.5")` first
- `.gitignore` — keep `models/` ignored

**Testing:**
- Delete `~/.cache/huggingface/`
- Run `sh install.sh` — should download from GitHub Releases
- Start server — should load model from `./models/` not HuggingFace
- Query should work

---

## Task B: MCP Tool — get_filing by accession_number

**Goal:** Add an MCP tool `get_filing` that lets agents retrieve all passages for a specific SEC filing by its accession number.

**Why:** When search returns a result, agents often want to see the full filing context. Currently they'd need to navigate to SEC.gov manually. This tool returns all indexed passages for a given accession number.

**Steps:**
1. Add a new function in `src/db.py`:
   ```python
   def get_filing_by_accession(accession_number: str) -> list[dict]:
       """Return all chunks for a given SEC filing accession number."""
       table = get_table()
       if table is None:
           return []
       safe = accession_number.replace("'", "''").replace("\\", "")
       results = table.search().where(f"accession_number = '{safe}'").limit(100).to_list()
       return [
           {...same mapping as search()...}
           for r in results
       ]
   ```
2. Add a new tool to `TOOLS` list in `src/mcp.py`:
   ```python
   {
       "name": "get_filing",
       "description": "Retrieve all passages from a specific SEC filing by its accession number. Free — no payment required.",
       "inputSchema": {
           "type": "object",
           "required": ["accession_number"],
           "properties": {
               "accession_number": {"type": "string", "maxLength": 50},
           },
       },
   }
   ```
3. Add handler for the tool in `mcp_handler()` in `src/mcp.py`:
   - Validate accession_number (alphanumeric + dashes only)
   - Call `get_filing_by_accession()`
   - Format results as text blocks
4. Mark the tool as free (no x402 payment) — it's just metadata retrieval

**Files to modify:**
- `src/db.py` — add `get_filing_by_accession()` function
- `src/mcp.py` — add tool definition and handler

**Testing:**
- Call tool with a known accession number from your data
- Should return all chunks for that filing
- Call with invalid accession number — returns empty list

---

## Task C: In-Memory LRU Cache for Query Results

**Goal:** Cache query results in memory so repeated queries (common in multi-agent systems) return instantly without re-embedding or vector search.

**Why:** Agents often ask the same questions. ~30% of production RAG queries are semantic duplicates. Caching cuts latency to sub-millisecond and reduces compute.

**Steps:**
1. Add `functools.lru_cache` wrapper around the core search function
2. Cache key: `(query_text, filing_type, company, top_k)` — immutable tuple
3. LRU size: 256 entries (~5MB of results cached)
4. Add cache hit/miss metrics to audit log
5. Add cache-bypass mechanism via header `X-Cache-Bypass: true`
6. Expose cache stats via admin endpoint

**Files to modify:**
- `src/query.py` — wrap search call with lru_cache
- `src/audit.py` — add cache_hit column, increment counters
- Admin dashboard — show cache hit rate

**Implementation pattern:**
```python
@functools.lru_cache(maxsize=256)
def _cached_search(query: str, filing_type: str | None, company: str | None, top_k: int) -> tuple:
    vec = embed_query(query)
    results = search(query_vector=vec, top_k=top_k, filing_type=filing_type, company=company)
    return tuple(frozenset(r.items()) for r in results)  # immutable
```

**Testing:**
- Same query twice → second is instant
- Different params → cache miss (new computation)
- Header `X-Cache-Bypass: true` → always compute
- Cache stats visible in admin dashboard

---

## Task D: Structured Error Response Codes

**Goal:** Return machine-readable error codes so agents can handle errors programmatically.

**Why:** Agents currently get generic 402/422/429/503. With error codes, agents can decide: retry? back off? fix input? give up?

**Steps:**
1. Define error codes enum in new file `src/errors.py`:
   ```python
   class ErrorCode(str, Enum):
       INVALID_FILING_TYPE = "INVALID_FILING_TYPE"
       INVALID_COMPANY = "INVALID_COMPANY"
       QUERY_TOO_LONG = "QUERY_TOO_LONG"
       QUERY_EMPTY = "QUERY_EMPTY"
       TOP_K_OUT_OF_RANGE = "TOP_K_OUT_OF_RANGE"
       UNKNOWN_FIELDS = "UNKNOWN_FIELDS"
       RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
       PAYMENT_REQUIRED = "PAYMENT_REQUIRED"
       PAYMENT_VERIFICATION_FAILED = "PAYMENT_VERIFICATION_FAILED"
       FACILITATOR_UNAVAILABLE = "FACILITATOR_UNAVAILABLE"
       REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
       INTERNAL_ERROR = "INTERNAL_ERROR"
   ```
2. Standard error response shape:
   ```json
   {
     "error": {
       "code": "INVALID_FILING_TYPE",
       "message": "filing_type must be one of: 10-K, 10-Q, 8-K",
       "retry": false,
       "field": "filing_type"
     }
   }
   ```
3. Field `retry`: boolean — true if agent should retry (e.g., 429, 503), false if input must change (e.g., 422)
4. Field `field`: which field had the problem (for validation errors)
5. Update FastAPI exception handlers to return new shape
6. Update rate limit handler
7. Keep HTTP status codes as-is — add structured body

**Files to modify:**
- New file: `src/errors.py`
- `src/server.py` — add exception handlers
- `src/query.py` — use error codes in responses

**Testing:**
- Send bad filing_type → get code `INVALID_FILING_TYPE`
- Send 61 requests in a minute → get code `RATE_LIMIT_EXCEEDED` with `retry: true`
- Send query > 1000 chars → get code `QUERY_TOO_LONG`

---

## Task E: Streaming Response for /v1/query (Server-Sent Events)

**Goal:** Stream results as they're computed so agents can start processing immediately.

**Why:** For `top_k=20`, response size is ~20KB. Streaming cuts perceived latency and lets agents process first results while more are arriving.

**Steps:**
1. Add new endpoint `/v1/query/stream` that returns SSE (Server-Sent Events)
2. Keep `/v1/query` unchanged (JSON response) for agents that prefer blocking
3. Use `fastapi.responses.StreamingResponse`
4. Each result sent as: `data: {json}\n\n`
5. End with: `data: [DONE]\n\n`

**Implementation:**
```python
from fastapi.responses import StreamingResponse

@router.post("/v1/query/stream")
async def query_stream(request: Request, body: QueryRequest):
    async def generate():
        query_vector = embed_query(body.query)
        results = search(
            query_vector=query_vector,
            top_k=body.top_k or 5,
            filing_type=body.filing_type,
            company=body.company,
        )
        for r in results:
            yield f"data: {json.dumps(r)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
```

**Files to modify:**
- `src/query.py` — add streaming endpoint
- `src/landing.py` — document streaming endpoint in API docs

**Testing:**
- `curl -N -X POST .../v1/query/stream -d '{"query":"revenue"}'`
- Should see results appear one by one
- Last line is `data: [DONE]`

---

## Implementation Priority

**Do in parallel (independent):**
1. **Task A** (model hosting) — infrastructure
2. **Task B** (get_filing tool) — MCP feature

**Do sequentially after A+B:**
3. **Task D** (error codes) — affects all endpoints
4. **Task C** (caching) — depends on error codes for cache bypass header validation
5. **Task E** (streaming) — adds new endpoint

## Security & Quality Requirements

All tasks must:
- Pass `ruff check src/`
- Pass `bandit -r src/` (except the known B615 false positive)
- Follow existing patterns in the codebase
- Not break the Replit deployment (no requirements.txt, no pyproject.toml changes that would trigger uv)
- Add tests to `tests/` if test infrastructure exists, otherwise add manual test steps to commit message
- Each task must end with a git commit + push

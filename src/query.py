from __future__ import annotations

import functools
import json
import logging
from pathlib import Path
from typing import Literal, Optional

import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.db import MODEL_NAME, search

_cache_stats = {"hits": 0, "misses": 0}
_CACHE_MAX_SIZE = 256

logger = logging.getLogger("edgar-rag")

router = APIRouter()

LOCAL_MODEL_DIR = Path("models/bge-small-en-v1.5")
LOCAL_MODEL_ONNX = LOCAL_MODEL_DIR / "onnx" / "model.onnx"
LOCAL_MODEL_TOKENIZER = LOCAL_MODEL_DIR / "tokenizer.json"


@functools.lru_cache(maxsize=1)
def _load_model():
    """Load embedding model. Uses sentence-transformers if available (local dev),
    falls back to ONNX runtime (Replit — no torch needed).
    """
    try:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model via sentence-transformers")
        return ("sbert", SentenceTransformer(MODEL_NAME))
    except ImportError:
        pass

    import onnxruntime as ort
    from tokenizers import Tokenizer

    if not (LOCAL_MODEL_ONNX.exists() and LOCAL_MODEL_TOKENIZER.exists()):
        msg = (
            f"Local ONNX model not found at {LOCAL_MODEL_DIR}. "
            "Run install.sh to download the model bundle from GitHub Releases, "
            "or install huggingface-hub and set up the HF Hub fallback."
        )
        raise RuntimeError(msg)

    logger.info("Loading embedding model via ONNX runtime from %s", LOCAL_MODEL_DIR)
    model_path = str(LOCAL_MODEL_ONNX)
    tokenizer = Tokenizer.from_file(str(LOCAL_MODEL_TOKENIZER))

    tokenizer.enable_padding()
    tokenizer.enable_truncation(max_length=512)

    # Tuned ONNX session options for CPU inference on constrained containers
    sess_options = ort.SessionOptions()
    sess_options.intra_op_num_threads = 2
    sess_options.inter_op_num_threads = 1
    sess_options.enable_mem_pattern = True
    sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(model_path, sess_options=sess_options)
    return ("onnx", session, tokenizer)


def embed_query(text: str) -> list[float]:
    loaded = _load_model()

    if loaded[0] == "sbert":
        model = loaded[1]
        vector = model.encode(text, normalize_embeddings=True)
        return vector.tolist()

    # ONNX path
    session, tokenizer = loaded[1], loaded[2]
    encoded = tokenizer.encode(text)
    input_ids = np.array([encoded.ids], dtype=np.int64)
    attention_mask = np.array([encoded.attention_mask], dtype=np.int64)
    token_type_ids = np.zeros_like(input_ids)

    outputs = session.run(
        None,
        {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        },
    )

    # Mean pooling + L2 normalize
    embeddings = outputs[0]  # (1, seq_len, hidden_dim)
    mask = attention_mask[:, :, np.newaxis].astype(np.float32)
    pooled = (embeddings * mask).sum(axis=1) / mask.sum(axis=1)
    norm = np.linalg.norm(pooled, axis=1, keepdims=True)
    normalized = pooled / norm
    return normalized[0].tolist()


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    filing_type: Optional[Literal["10-K", "10-Q", "8-K"]] = None
    company: Optional[str] = Field(default=None, min_length=1, max_length=200)
    top_k: Optional[int] = Field(default=5, ge=1, le=20)

    class Config:
        extra = "forbid"


class QueryResult(BaseModel):
    text: str
    score: float
    company: str
    cik: str
    filing_type: str
    filing_date: str
    section: str
    source_url: str


class QueryResponse(BaseModel):
    query: str
    result_count: int
    results: list[QueryResult]


@functools.lru_cache(maxsize=_CACHE_MAX_SIZE)
def _cached_search(
    query: str,
    filing_type: str | None,
    company: str | None,
    top_k: int,
) -> tuple[dict, ...]:
    vec = embed_query(query)
    results = search(
        query_vector=vec,
        top_k=top_k,
        filing_type=filing_type,
        company=company,
    )
    return tuple(results)


@router.post("/v1/query", response_model=QueryResponse)
async def query_filings(request: Request, body: QueryRequest) -> QueryResponse:
    top_k = body.top_k or 5
    bypass = request.headers.get("x-cache-bypass", "").lower() == "true"

    if bypass:
        query_vector = embed_query(body.query)
        results = search(
            query_vector=query_vector,
            top_k=top_k,
            filing_type=body.filing_type,
            company=body.company,
        )
    else:
        info_before = _cached_search.cache_info()
        cached = _cached_search(body.query, body.filing_type, body.company, top_k)
        info_after = _cached_search.cache_info()
        if info_after.hits > info_before.hits:
            _cache_stats["hits"] += 1
        else:
            _cache_stats["misses"] += 1
        # Return copies so callers can't mutate cached dicts
        results = [dict(r) for r in cached]

    typed = [QueryResult(**r) for r in results]
    return QueryResponse(query=body.query, result_count=len(typed), results=typed)


@router.post("/v1/query/stream")
async def query_stream(request: Request, body: QueryRequest) -> StreamingResponse:
    top_k = body.top_k or 5

    async def generate():
        query_vector = embed_query(body.query)
        results = search(
            query_vector=query_vector,
            top_k=top_k,
            filing_type=body.filing_type,
            company=body.company,
        )
        metadata = {
            "type": "metadata",
            "query": body.query,
            "result_count": len(results),
        }
        yield f"data: {json.dumps(metadata)}\n\n"
        for r in results:
            payload = {"type": "result", **r}
            yield f"data: {json.dumps(payload)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/v1/query/cache-stats")
async def query_cache_stats() -> dict:
    info = _cached_search.cache_info()
    hits = _cache_stats["hits"]
    misses = _cache_stats["misses"]
    total = hits + misses
    hit_rate = (hits / total) if total > 0 else 0.0
    return {
        "hits": hits,
        "misses": misses,
        "hit_rate": round(hit_rate, 4),
        "cache_size": info.currsize,
        "max_size": info.maxsize,
    }

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Literal, Optional

import numpy as np
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from src.db import MODEL_NAME, search

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

    if LOCAL_MODEL_ONNX.exists() and LOCAL_MODEL_TOKENIZER.exists():
        logger.info(
            "Loading embedding model via ONNX runtime (loading from local path %s)",
            LOCAL_MODEL_DIR,
        )
        model_path = str(LOCAL_MODEL_ONNX)
        tokenizer = Tokenizer.from_file(str(LOCAL_MODEL_TOKENIZER))
    else:
        logger.info("Loading embedding model via ONNX runtime (downloading from HuggingFace Hub)")
        from huggingface_hub import hf_hub_download

        model_path = hf_hub_download(
            repo_id=MODEL_NAME, filename="onnx/model.onnx", revision="main"
        )
        tokenizer = Tokenizer.from_pretrained(MODEL_NAME)

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


@router.post("/v1/query", response_model=QueryResponse)
async def query_filings(request: Request, body: QueryRequest) -> QueryResponse:
    query_vector = embed_query(body.query)
    results = search(
        query_vector=query_vector,
        top_k=body.top_k or 5,
        filing_type=body.filing_type,
        company=body.company,
    )
    typed = [QueryResult(**r) for r in results]
    return QueryResponse(query=body.query, result_count=len(typed), results=typed)

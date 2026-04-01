from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.db import search

logger = logging.getLogger("edgar-rag")

router = APIRouter()

_model = None


def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer("BAAI/bge-small-en-v1.5")
    return _model


def embed_query(text: str) -> list[float]:
    model = get_model()
    vector = model.encode(text, normalize_embeddings=True)
    return vector.tolist()


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
    results: list[QueryResult]


limiter = Limiter(key_func=get_remote_address)


@router.post("/v1/query", response_model=QueryResponse)
@limiter.limit("60/minute")
async def query_filings(request: Request, body: QueryRequest) -> QueryResponse:
    query_vector = embed_query(body.query)
    results = search(
        query_vector=query_vector,
        top_k=body.top_k or 5,
        filing_type=body.filing_type,
        company=body.company,
    )
    return QueryResponse(results=[QueryResult(**r) for r in results])
